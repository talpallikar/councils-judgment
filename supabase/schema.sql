-- Council's Judgment (MTG art voting) — Supabase schema.
-- Paste this whole file into your Supabase project's SQL editor and run it once.
--
-- Design: the anon key (published in config.js) can NOT touch the votes table
-- directly — row level security is on and no policies are granted. All access
-- goes through two SECURITY DEFINER functions that validate their inputs:
--   record_vote(...)  insert one vote, returns the pairing's community split
--   get_stats(fmt)    aggregate rankings for one format or 'all'

create table if not exists votes (
  id bigint generated always as identity primary key,
  format text not null,
  card_name text not null,
  winner_id uuid not null,
  loser_id uuid not null,
  winner_artist text, winner_set text, winner_art text,
  loser_artist  text, loser_set  text, loser_art  text,
  created_at timestamptz not null default now()
);
create index if not exists votes_card_idx on votes (card_name);
create index if not exists votes_format_idx on votes (format);

alter table votes enable row level security;
revoke all on table votes from anon, authenticated;

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

create or replace function record_vote(
  p_format text, p_card text, p_winner jsonb, p_loser jsonb
) returns jsonb
language plpgsql volatile security definer set search_path = public
as $$
declare
  wid uuid; lid uuid;
  agree int; disagree int; card_total int;
begin
  if p_format not in ('standard','pioneer','modern','legacy','vintage','commander') then
    raise exception 'unknown format';
  end if;
  if p_card is null or char_length(p_card) = 0 or char_length(p_card) > 200 then
    raise exception 'bad card name';
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

  insert into votes (format, card_name, winner_id, loser_id,
                     winner_artist, winner_set, winner_art,
                     loser_artist, loser_set, loser_art)
  values (p_format, p_card, wid, lid,
          left(p_winner->>'artist', 120), left(p_winner->>'set_name', 120),
          left(p_winner->>'art', 300),
          left(p_loser->>'artist', 120), left(p_loser->>'set_name', 120),
          left(p_loser->>'art', 300));

  select count(*) into agree
    from votes where card_name = p_card and winner_id = wid and loser_id = lid;
  select count(*) into disagree
    from votes where card_name = p_card and winner_id = lid and loser_id = wid;
  select count(*) into card_total from votes where card_name = p_card;

  return jsonb_build_object(
    'ok', true,
    'pair', jsonb_build_object(
      'with_you', agree,
      'against_you', disagree,
      'agree_pct', round(100.0 * agree / (agree + disagree))),
    'card_votes', card_total);
end
$$;

create or replace function get_stats(fmt text default 'all')
returns jsonb
language sql stable security definer set search_path = public
as $$
with v as (
  select * from votes where fmt = 'all' or format = fmt
),
totals as (select count(*)::int n from v),
mg as (
  select case when n >= 200 then 5 when n >= 60 then 3 else 1 end m from totals
),
sides as (
  select winner_id id, card_name, winner_artist artist, winner_set set_name,
         winner_art art, 1 w, 0 l from v
  union all
  select loser_id, card_name, loser_artist, loser_set, loser_art, 0, 1 from v
),
per_art as (
  select id, max(card_name) card_name, max(artist) artist,
         max(set_name) set_name, max(art) art,
         sum(w)::int wins, sum(l)::int losses, count(*)::int games
  from sides group by id
),
ranked as (
  select *, round(100.0 * wins / games)::int win_rate, wilson_lb(wins, games) score
  from per_art
),
pairs as (
  select card_name,
         least(winner_id, loser_id) a, greatest(winner_id, loser_id) b,
         count(*) filter (where winner_id <= loser_id)::int na,
         count(*) filter (where winner_id >  loser_id)::int nb,
         count(*)::int n
  from v group by 1, 2, 3
),
pair_rows as (
  select p.card_name, p.n, p.na, p.nb,
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
    'cards', (select count(distinct card_name) from v),
    'arts',  (select count(*) from per_art),
    'by_format', coalesce((select jsonb_object_agg(format, c)
                           from (select format, count(*)::int c from v group by format) f),
                          '{}'::jsonb)),
  'top_arts', coalesce((select jsonb_agg(to_jsonb(t))
    from (select id, card_name, artist, set_name, art, wins, losses, games, win_rate
          from ranked
          where games >= (select m from mg)
          order by wilson_lb(wins, games) desc, games desc
          limit 12) t), '[]'::jsonb),
  'closest', coalesce((select jsonb_agg(jsonb_build_object(
      'card_name', card_name, 'n', n,
      'a', jsonb_build_object('artist', a_artist, 'set_name', a_set, 'art', a_art,
                              'votes', na, 'pct', a_pct),
      'b', jsonb_build_object('artist', b_artist, 'set_name', b_set, 'art', b_art,
                              'votes', nb, 'pct', b_pct)))
    from (select * from pair_rows
          order by abs(na::float8 / n - 0.5) asc, n desc
          limit 8) c), '[]'::jsonb),
  'blowouts', coalesce((select jsonb_agg(jsonb_build_object(
      'card_name', card_name, 'n', n,
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
    from (select card_name, count(*)::int votes
          from v group by card_name
          order by votes desc limit 10) t), '[]'::jsonb)
)
$$;

revoke all on function record_vote(text, text, jsonb, jsonb) from public;
revoke all on function get_stats(text) from public;
grant execute on function record_vote(text, text, jsonb, jsonb) to anon, authenticated;
grant execute on function get_stats(text) to anon, authenticated;
