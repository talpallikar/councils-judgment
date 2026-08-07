-- Council's Judgment (MTG art voting) — Supabase schema.
-- Paste this whole file into your Supabase project's SQL editor and run it once.
--
-- Design: the anon key (published in config.js) can NOT touch the votes table
-- directly — row level security is on and no policies are granted. All access
-- goes through two SECURITY DEFINER functions that validate their inputs:
--   record_vote(...)  insert one vote, returns the pairing's community split
--   get_stats(fmt)    aggregate rankings for one format or 'all'

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists votes (
  id bigint generated always as identity primary key,
  format text not null,
  card_name text not null,
  winner_id uuid not null,
  loser_id uuid not null,
  winner_artist text, winner_set text, winner_art text,
  loser_artist  text, loser_set  text, loser_art  text,
  voter_hash text,   -- sha256(salt + caller IP); raw IPs are never stored
  user_id uuid,      -- set when the voter is signed in (optional accounts)
  created_at timestamptz not null default now()
);
-- Idempotent upgrades: "create table if not exists" cannot add columns to a
-- table made by an earlier schema version, so every late-added column gets
-- an alter here, before anything references it.
alter table votes add column if not exists voter_hash text;
alter table votes add column if not exists user_id uuid;
-- cross-card duels: the loser art's card when it differs from card_name
-- (null = classic same-card vote)
alter table votes add column if not exists loser_card_name text;
create index if not exists votes_card_idx on votes (card_name);
create index if not exists votes_format_idx on votes (format);
create index if not exists votes_voter_idx on votes (voter_hash, created_at);

alter table votes enable row level security;
revoke all on table votes from anon, authenticated;

-- Per-art Elo ledger (K=32, start 1500), updated on every vote.
create table if not exists arts (
  illustration_id uuid primary key,
  card_name text not null,
  artist text, set_name text, art_url text,
  wins int not null default 0,
  losses int not null default 0,
  elo double precision not null default 1500
);
alter table arts enable row level security;
revoke all on table arts from anon, authenticated;

-- Random per-project salt for IP hashing, generated once at install.
create table if not exists app_secrets (salt text not null);
insert into app_secrets (salt)
  select gen_random_uuid()::text where not exists (select 1 from app_secrets);
alter table app_secrets enable row level security;
revoke all on table app_secrets from anon, authenticated;

-- Lower bound of the Wilson score interval (z = 1.96): win-rate ranking that
-- penalizes small samples, so 3-0 does not outrank 40-10.
create or replace function wilson_lb(wins bigint, games bigint)
returns double precision
language sql immutable
as $$
  select case when games = 0 then 0.0 else
    ( (wins::float8 / games) + 3.841459 / (2 * games)
      - 1.959964 * sqrt(((wins::float8 / games) * (1 - wins::float8 / games)
                         + 3.841459 / (4 * games)) / games)
    ) / (1 + 3.841459 / games)
  end
$$;

-- signature gained p_loser_card; drop the old overload for unambiguous RPC
drop function if exists record_vote(text, text, jsonb, jsonb);

create or replace function record_vote(
  p_format text, p_card text, p_winner jsonb, p_loser jsonb,
  p_loser_card text default null
) returns jsonb
language plpgsql volatile security definer set search_path = public, extensions
as $$
declare
  wid uuid; lid uuid;
  agree int; disagree int; card_total int;
  hdrs jsonb; ip text; vhash text;
  w_elo double precision; l_elo double precision; delta double precision;
begin
  if p_format not in ('standard','pioneer','modern','legacy','vintage','commander','any')
     and p_format !~ '^set:[a-z0-9]{2,6}$' then
    raise exception 'unknown format';
  end if;
  if p_card is null or char_length(p_card) = 0 or char_length(p_card) > 200 then
    raise exception 'bad card name';
  end if;
  if p_loser_card is not null
     and (char_length(p_loser_card) = 0 or char_length(p_loser_card) > 200
          or p_loser_card = p_card) then
    raise exception 'bad loser card name';
  end if;
  wid := (p_winner->>'id')::uuid;   -- casts also reject malformed ids
  lid := (p_loser->>'id')::uuid;
  if wid = lid then
    raise exception 'identical arts';
  end if;
  if (p_winner->>'art') not like 'https://cards.scryfall.io/%'
     or (p_loser->>'art') not like 'https://cards.scryfall.io/%' then
    raise exception 'art must be a Scryfall image';
  end if;

  -- Rate limiting by salted IP hash. PostgREST exposes request headers;
  -- when absent (e.g. running in the SQL editor) the throttle is skipped.
  hdrs := nullif(current_setting('request.headers', true), '')::jsonb;
  if hdrs is not null then
    ip := trim(coalesce(hdrs->>'cf-connecting-ip', hdrs->>'x-real-ip',
                        split_part(hdrs->>'x-forwarded-for', ',', 1)));
  end if;
  if ip is not null and ip <> '' then
    vhash := encode(digest((select salt from app_secrets limit 1) || ip, 'sha256'), 'hex');
    if (select count(*) from votes where voter_hash = vhash
        and created_at > now() - interval '1 minute') >= 15 then
      raise exception 'Too many votes in the last minute. Try again shortly.';
    end if;
    if (select count(*) from votes where voter_hash = vhash
        and created_at > now() - interval '1 hour') >= 250 then
      raise exception 'Hourly vote limit reached.';
    end if;
    if (select count(*) from votes where voter_hash = vhash
        and winner_id in (wid, lid) and loser_id in (wid, lid)
        and created_at > now() - interval '1 day') >= 3 then
      raise exception 'Daily limit reached for this pairing.';
    end if;
  end if;

  insert into votes (format, card_name, winner_id, loser_id,
                     winner_artist, winner_set, winner_art,
                     loser_artist, loser_set, loser_art, voter_hash, user_id,
                     loser_card_name)
  values (p_format, p_card, wid, lid,
          left(p_winner->>'artist', 120), left(p_winner->>'set_name', 120),
          left(p_winner->>'art', 300),
          left(p_loser->>'artist', 120), left(p_loser->>'set_name', 120),
          left(p_loser->>'art', 300), vhash, auth.uid(), p_loser_card);

  -- Elo update (metadata comes from the same validated payload as the vote)
  insert into arts (illustration_id, card_name, artist, set_name, art_url) values
    (wid, p_card, left(p_winner->>'artist', 120), left(p_winner->>'set_name', 120),
     left(p_winner->>'art', 300)),
    (lid, coalesce(p_loser_card, p_card),
     left(p_loser->>'artist', 120), left(p_loser->>'set_name', 120),
     left(p_loser->>'art', 300))
  on conflict (illustration_id) do nothing;
  select elo into w_elo from arts where illustration_id = wid;
  select elo into l_elo from arts where illustration_id = lid;
  delta := 32.0 * (1.0 - 1.0 / (1.0 + power(10, (l_elo - w_elo) / 400.0)));
  update arts set wins = wins + 1, elo = elo + delta where illustration_id = wid;
  update arts set losses = losses + 1, elo = elo - delta where illustration_id = lid;

  -- pairings are keyed by illustration ids alone (they are globally unique),
  -- which lets same-card and cross-card votes share one ledger
  select count(*) into agree
    from votes where winner_id = wid and loser_id = lid;
  select count(*) into disagree
    from votes where winner_id = lid and loser_id = wid;
  select count(*) into card_total
    from votes where card_name = p_card or loser_card_name = p_card;

  return jsonb_build_object(
    'ok', true,
    'pair', jsonb_build_object(
      'with_you', agree,
      'against_you', disagree,
      'agree_pct', round(100.0 * agree / (agree + disagree))),
    'card_votes', card_total,
    -- cross-card mode: return the loser card's total too so the UI can
    -- credit both cards. null for same-card votes.
    'loser_card_votes', case when p_loser_card is null then null else
      (select count(*) from votes
       where card_name = p_loser_card or loser_card_name = p_loser_card)
    end,
    'cross', p_loser_card is not null);
end
$$;

-- One-time Elo backfill: replay pre-existing votes in order. Skipped whenever
-- the arts table already has rows, so re-running the schema never
-- double-applies.
do $$
declare
  v record; w_elo double precision; l_elo double precision; d double precision;
begin
  if exists (select 1 from arts) then return; end if;
  for v in select * from votes order by id loop
    insert into arts (illustration_id, card_name, artist, set_name, art_url) values
      (v.winner_id, v.card_name, v.winner_artist, v.winner_set, v.winner_art),
      (v.loser_id, v.card_name, v.loser_artist, v.loser_set, v.loser_art)
    on conflict (illustration_id) do nothing;
    select elo into w_elo from arts where illustration_id = v.winner_id;
    select elo into l_elo from arts where illustration_id = v.loser_id;
    d := 32.0 * (1.0 - 1.0 / (1.0 + power(10, (l_elo - w_elo) / 400.0)));
    update arts set wins = wins + 1, elo = elo + d where illustration_id = v.winner_id;
    update arts set losses = losses + 1, elo = elo - d where illustration_id = v.loser_id;
  end loop;
end $$;

-- signature changed when p_mine was added; drop the old overload so RPC
-- name resolution stays unambiguous
drop function if exists get_stats(text);

create or replace function get_stats(fmt text default 'all', p_mine boolean default false)
returns jsonb
language sql stable security definer set search_path = public
as $$
with v as (
  select * from votes
  where (fmt = 'all' or format = fmt)
    and (not p_mine or user_id = auth.uid())
),
totals as (select count(*)::int n from v),
mg as (
  select case when n >= 200 then 5 when n >= 60 then 3 else 1 end m from totals
),
sides as (
  select id vid, winner_id id, card_name, winner_artist artist,
         winner_set set_name, winner_art art, 1 w, 0 l from v
  union all
  select id, loser_id, coalesce(loser_card_name, card_name),
         loser_artist, loser_set, loser_art, 0, 1 from v
),
per_art as (
  select id, max(card_name) card_name, max(artist) artist,
         max(set_name) set_name, max(art) art,
         sum(w)::int wins, sum(l)::int losses, count(*)::int games
  from sides group by id
),
ranked as (
  select p.*, round(100.0 * p.wins / p.games)::int win_rate,
         wilson_lb(p.wins, p.games) score,
         round(coalesce(a.elo, 1500))::int elo
  from per_art p
  left join arts a on a.illustration_id = p.id
),
pairs as (
  select least(winner_id, loser_id) a, greatest(winner_id, loser_id) b,
         count(*) filter (where winner_id <= loser_id)::int na,
         count(*) filter (where winner_id >  loser_id)::int nb,
         count(*)::int n
  from v group by 1, 2
),
pair_rows as (
  select case when ja.card_name = jb.card_name then ja.card_name
              else ja.card_name || ' vs ' || jb.card_name end card_name,
         (ja.card_name is distinct from jb.card_name) is_cross,
         p.n, p.na, p.nb,
         round(100.0 * p.na / p.n)::int a_pct, round(100.0 * p.nb / p.n)::int b_pct,
         ja.artist a_artist, ja.set_name a_set, ja.art a_art,
         jb.artist b_artist, jb.set_name b_set, jb.art b_art
  from pairs p
  left join per_art ja on ja.id = p.a
  left join per_art jb on jb.id = p.b
  where p.n >= greatest(3, (select m from mg))
)
select jsonb_build_object(
  'format', fmt,
  'min_games', (select m from mg),
  'totals', jsonb_build_object(
    'votes', (select n from totals),
    'cards', (select count(distinct card_name) from sides),
    'arts',  (select count(*) from per_art),
    'by_format', coalesce((select jsonb_object_agg(format, c)
                           from (select format, count(*)::int c from v group by format) f),
                          '{}'::jsonb),
    'same_card_votes', (select count(*)::int from v where loser_card_name is null),
    'cross_card_votes', (select count(*)::int from v where loser_card_name is not null)),
  'top_arts', coalesce((select jsonb_agg(to_jsonb(t))
    from (select id, card_name, artist, set_name, art, wins, losses, games,
                 win_rate, elo
          from ranked
          where games >= (select m from mg)
          order by wilson_lb(wins, games) desc, games desc
          limit 12) t), '[]'::jsonb),
  'closest', coalesce((select jsonb_agg(jsonb_build_object(
      'card_name', card_name, 'n', n, 'cross', is_cross,
      'a', jsonb_build_object('artist', a_artist, 'set_name', a_set, 'art', a_art,
                              'votes', na, 'pct', a_pct),
      'b', jsonb_build_object('artist', b_artist, 'set_name', b_set, 'art', b_art,
                              'votes', nb, 'pct', b_pct)))
    from (select * from pair_rows
          order by abs(na::float8 / n - 0.5) asc, n desc
          limit 8) c), '[]'::jsonb),
  'blowouts', coalesce((select jsonb_agg(jsonb_build_object(
      'card_name', card_name, 'n', n, 'cross', is_cross,
      'a', jsonb_build_object('artist', a_artist, 'set_name', a_set, 'art', a_art,
                              'votes', na, 'pct', a_pct),
      'b', jsonb_build_object('artist', b_artist, 'set_name', b_set, 'art', b_art,
                              'votes', nb, 'pct', b_pct)))
    from (select * from pair_rows
          where greatest(a_pct, b_pct) >= 65
          order by greatest(a_pct, b_pct) desc, n desc
          limit 8) c), '[]'::jsonb),
  'top_artists', coalesce((select jsonb_agg(to_jsonb(t))
    from (select artist, count(*)::int arts,
                 sum(wins)::int wins, sum(losses)::int losses,
                 (sum(wins) + sum(losses))::int games,
                 round(100.0 * sum(wins) / (sum(wins) + sum(losses)))::int win_rate
          from ranked
          where artist is not null
          group by artist
          having sum(wins) + sum(losses) >= greatest(5, (select m from mg))
          order by wilson_lb(sum(wins), sum(wins) + sum(losses)) desc
          limit 10) t), '[]'::jsonb),
  'most_voted', coalesce((select jsonb_agg(to_jsonb(t))
    from (select card_name, count(distinct vid)::int votes
          from sides group by card_name
          order by votes desc limit 10) t), '[]'::jsonb),
  -- How often the signed-in caller's picks match the community majority on
  -- the pairings they voted on (their own vote excluded). Null when anonymous.
  'alignment', (case when auth.uid() is null then null else (
    select jsonb_build_object(
      'scored', count(*) filter (where w_n <> a_n)::int,
      'agreed', count(*) filter (where w_n > a_n)::int)
    from (
      select
        (select count(*) from votes o
         where o.winner_id = uv.winner_id
           and o.loser_id = uv.loser_id and o.id <> uv.id) w_n,
        (select count(*) from votes o
         where o.winner_id = uv.loser_id
           and o.loser_id = uv.winner_id) a_n
      from votes uv where uv.user_id = auth.uid()) t
  ) end),
  -- Consecutive correct calls (pick matched the community majority), over the
  -- caller's scored votes in vote order. Judged against today's counts, so
  -- old streaks can shift as the community keeps voting.
  'streak', (case when auth.uid() is null then null else (
    with mine as (
      select uv.id,
        (select count(*) from votes o
         where o.winner_id = uv.winner_id
           and o.loser_id = uv.loser_id and o.id <> uv.id) w_n,
        (select count(*) from votes o
         where o.winner_id = uv.loser_id
           and o.loser_id = uv.winner_id) a_n
      from votes uv where uv.user_id = auth.uid()
    ), scored as (
      select id, (w_n > a_n) correct, row_number() over (order by id) rn
      from mine where w_n <> a_n
    ), islands as (
      select correct, count(*)::int len, max(rn) mx
      from (select *, rn - row_number() over (partition by correct order by rn) grp
            from scored) g
      group by correct, grp
    )
    select jsonb_build_object(
      'current', coalesce((select len from islands
                           where correct and mx = (select max(rn) from scored)), 0),
      'best', coalesce((select max(len) from islands where correct), 0))
  ) end)
)
$$;

revoke all on function record_vote(text, text, jsonb, jsonb, text) from public;
revoke all on function get_stats(text, boolean) from public;
grant execute on function record_vote(text, text, jsonb, jsonb, text) to anon, authenticated;
grant execute on function get_stats(text, boolean) to anon, authenticated;
