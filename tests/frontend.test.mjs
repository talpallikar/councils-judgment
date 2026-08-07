// Frontend logic tests: loads the real <script> from docs/index.html under
// Node with DOM/network stubs, then exercises the pure logic — art
// extraction, matchup building, Wilson scoring, solo-mode votes and stats.
// Run: node --test tests/
import { test, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = n => JSON.parse(readFileSync(join(HERE, "fixtures", n), "utf8"));

// ---- browser stubs ----
const dummyEl = () => new Proxy(
  { classList: { toggle() {}, add() {}, remove() {} }, style: {} },
  { get(t, k) { if (k in t) return t[k];
      if (k === "appendChild" || k === "prepend" || k === "focus") return () => {};
      return t[k]; },
    set() { return true; } });
global.document = { querySelector: () => dummyEl(), addEventListener() {},
                    createElement: () => dummyEl(), body: { appendChild() {} } };
global.localStorage = {
  _m: {},
  getItem(k) { return this._m[k] ?? null; },
  setItem(k, v) { this._m[k] = String(v); },
  removeItem(k) { delete this._m[k]; },
};
global.window = { MTGART_CONFIG: { supabaseUrl: "", supabaseAnonKey: "" },
                  addEventListener() {} };
global.location = { hash: "", pathname: "/", search: "" };
global.history = { replaceState() {}, pushState() {} };
global.Image = class { set src(v) {} };
global.requestAnimationFrame = f => f();

// Network: staples.json and Scryfall answered from fixtures, no real network.
const staplesFixture = {
  generated_at: 1700000000,
  formats: [
    { key: "modern", label: "Modern", source: "test", updated_at: 1700000000,
      names: ["Lightning Bolt", "Delver of Secrets"] },
    { key: "legacy", label: "Legacy", source: "test", updated_at: 1700000000,
      names: ["Delver of Secrets"] },
    { key: "vintage", label: "Vintage", source: "test", updated_at: null, names: [] },
  ],
};
global.fetch = url => {
  const u = String(url);
  let payload;
  if (u === "staples.json") payload = staplesFixture;
  else if (u.includes("cards/random")) payload = FIX("scryfall_bolt.json").data[0];
  else if (u.includes("Lightning")) payload = FIX("scryfall_bolt.json");
  else if (u.includes("Delver")) payload = FIX("scryfall_delver.json");
  else throw new Error("unexpected fetch in test: " + u);
  return Promise.resolve({ ok: true, status: 200, json: async () => payload });
};

let T;
before(async () => {
  const html = readFileSync(join(HERE, "..", "docs", "index.html"), "utf8");
  let src = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('"use strict";', "");
  src += "\nglobalThis.__T = { state, extractArt, wilson, newMatchup, recordVote, localStats, localCard, localArtist, getPrints, bumpStreak };";
  (0, eval)(src);
  T = globalThis.__T;
  // Boot's loadHome chain includes a Scryfall rate-limit sleep of up to
  // 120ms (the /sets fetch); wait it out fully, or boot's "resume last
  // pool" check can fire startDuel mid-suite once a test sets state.format
  // and race the tests' own draws.
  await new Promise(r => setTimeout(r, 400));
});

beforeEach(() => {
  global.localStorage._m = {};
});

// ---- extractArt ----
test("extractArt: normal card yields id, artist, images and Scryfall page", () => {
  const card = FIX("scryfall_bolt.json").data[0];
  const e = T.extractArt(card, "Lightning Bolt");
  assert.ok(e.id);
  assert.ok(e.artist);
  assert.match(e.art, /^https:\/\/cards\.scryfall\.io\//);
  assert.match(e.full, /^https:\/\/cards\.scryfall\.io\//);
  assert.match(e.page, /^https:\/\/scryfall\.com\//);
});

test("extractArt: transform card resolves the matching face", () => {
  const cards = FIX("scryfall_delver.json").data.filter(c => c.layout === "transform");
  assert.ok(cards.length >= 1, "fixture needs a transform card");
  const e = T.extractArt(cards[0], "Delver of Secrets");
  assert.ok(e, "transform card must yield an art entry");
  const face = cards[0].card_faces.find(f => f.name === "Delver of Secrets");
  assert.equal(e.id, face.illustration_id);
  assert.equal(e.art, face.image_uris.art_crop);
});

test("extractArt: card without illustration id is skipped", () => {
  const card = structuredClone(FIX("scryfall_bolt.json").data[0]);
  delete card.illustration_id;
  assert.equal(T.extractArt(card, "Lightning Bolt"), null);
});

// ---- wilson ----
test("wilson: zero games scores zero", () => {
  assert.equal(T.wilson(0, 0), 0);
});

test("wilson: larger sample beats equal win-rate small sample", () => {
  assert.ok(T.wilson(40, 50) > T.wilson(4, 5));
});

test("wilson: 3-0 does not outrank 40-10", () => {
  assert.ok(T.wilson(40, 50) > T.wilson(3, 3));
});

// ---- matchup ----
test("newMatchup: returns two distinct arts of the same card", async () => {
  T.state.format = "modern";
  const m = await T.newMatchup();
  assert.ok(["Lightning Bolt", "Delver of Secrets"].includes(m.card));
  assert.equal(m.arts.length, 2);
  assert.notEqual(m.arts[0].id, m.arts[1].id);
  assert.ok(m.total_arts >= 2);
});

test("newMatchup: cycles through the pool before repeating a card", async () => {
  T.state.format = "modern";   // two-card pool
  const first = (await T.newMatchup()).card;
  const seenAfter = JSON.parse(global.localStorage._m["mtgart.seen.modern"] || "[]");
  assert.deepEqual(seenAfter, [first],
    "exactly the drawn card should be marked seen — anything else means a " +
    "fixture card was skipped as having <2 arts");
  const second = (await T.newMatchup()).card;
  assert.notEqual(first, second, "second draw must differ in a 2-card pool");
  const third = (await T.newMatchup()).card;  // pool exhausted -> cycles
  assert.ok(["Lightning Bolt", "Delver of Secrets"].includes(third));
});

test("newMatchup: format with empty staple list rejects", async () => {
  T.state.format = "vintage";
  await assert.rejects(() => T.newMatchup());
});

test("newMatchup: 'any' pool draws via Scryfall random", async () => {
  T.state.format = "any";
  const m = await T.newMatchup();
  assert.equal(m.format, "any");
  assert.equal(m.card, "Lightning Bolt");   // stubbed random card
  assert.notEqual(m.arts[0].id, m.arts[1].id);
});

test("getPrints: second call is served from localStorage cache", async () => {
  T.state.format = "modern";
  const first = await T.getPrints("Lightning Bolt");
  const realFetch = global.fetch;
  global.fetch = () => { throw new Error("network hit — cache miss"); };
  try {
    const second = await T.getPrints("Lightning Bolt");
    assert.deepEqual(second, first);
  } finally {
    global.fetch = realFetch;
  }
});

// ---- streak ----
test("bumpStreak: counts correct runs, resets on miss, skips unscored", () => {
  assert.deepEqual(T.bumpStreak(true, true), { cur: 1, best: 1 });
  assert.deepEqual(T.bumpStreak(true, true), { cur: 2, best: 2 });
  assert.deepEqual(T.bumpStreak(false, false), { cur: 2, best: 2 }); // unscored: no change
  assert.deepEqual(T.bumpStreak(true, false), { cur: 0, best: 2 }); // miss: reset
  assert.deepEqual(T.bumpStreak(true, true), { cur: 1, best: 2 });
});

// ---- solo votes & stats ----
function art(id, artist = "Artist " + id) {
  return { id, artist, set_name: "Set " + id,
           art: "https://cards.scryfall.io/a/" + id };
}
const matchup = (card, a, b, format = "modern") =>
  ({ format, card, total_arts: 2, arts: [a, b] });

test("recordVote (solo): pair agreement math", async () => {
  const [a, b] = [art("a1"), art("b1")];
  const m = matchup("Test Card", a, b);
  let r = await T.recordVote(m, a, b);
  assert.deepEqual(r.pair, { with_you: 1, against_you: 0, agree_pct: 100 });
  r = await T.recordVote(m, b, a);   // disagreeing vote
  assert.equal(r.pair.with_you, 1);
  assert.equal(r.pair.against_you, 1);
  assert.equal(r.pair.agree_pct, 50);
  assert.equal(r.card_votes, 2);
});

test("localStats: totals, ranking and pair aggregation", async () => {
  const [a, b] = [art("a2", "Alice"), art("b2", "Bob")];
  const m = matchup("Stat Card", a, b);
  for (let i = 0; i < 3; i++) await T.recordVote(m, a, b);
  await T.recordVote(m, b, a);
  const s = T.localStats("all");
  assert.equal(s.totals.votes, 4);
  assert.equal(s.totals.cards, 1);
  assert.equal(s.totals.arts, 2);
  assert.deepEqual(s.totals.by_format, { modern: 4 });
  assert.equal(s.top_arts[0].id, "a2");        // 3-1 beats 1-3
  assert.equal(s.top_arts[0].win_rate, 75);
  assert.ok(s.top_arts[0].elo > 1500, "winner's Elo should rise above 1500");
  const loserArt = s.top_arts.find(e => e.id === "b2");
  if (loserArt) assert.ok(loserArt.elo < 1500, "loser's Elo should fall");
  const pair = s.closest[0];
  assert.equal(pair.n, 4);
  assert.equal(pair.a.votes + pair.b.votes, 4);
});

test("localStats: cross-card votes count both cards and label pairs", async () => {
  const a = { ...art("11111111-0000-0000-0000-00000000000a", "Alice"), card: "Card One" };
  const b = { ...art("11111111-0000-0000-0000-00000000000b", "Bob"), card: "Card Two" };
  const m = { format: "modern", mode: "cross", card: "Card One vs Card Two",
              cards: ["Card One", "Card Two"], total_arts: 2, arts: [a, b] };
  const r = await T.recordVote(m, a, b);
  assert.equal(r.cross, true);
  assert.equal(r.card_votes, 1);
  assert.equal(r.loser_card_votes, 1);
  for (let i = 0; i < 2; i++) await T.recordVote(m, a, b);
  const s = T.localStats("all");
  assert.equal(s.totals.votes, 3);
  assert.equal(s.totals.cards, 2);
  assert.equal(s.totals.cross_card_votes, 3);
  assert.equal(s.totals.same_card_votes, 0);
  const mv = Object.fromEntries(s.most_voted.map(c => [c.card_name, c.votes]));
  assert.equal(mv["Card One"], 3);
  assert.equal(mv["Card Two"], 3);
  assert.ok(s.blowouts[0].card_name.includes(" vs "),
    "cross pairs should be labeled 'A vs B'");
  assert.equal(s.blowouts[0].cross, true);
});

test("localStats: same-card votes report loser_card_votes null and cross false", async () => {
  const [a, b] = [art("s1"), art("s2")];
  const r = await T.recordVote(matchup("Same Card", a, b), a, b);
  assert.equal(r.cross, false);
  assert.equal(r.loser_card_votes, null);
  const s = T.localStats("all");
  assert.equal(s.totals.same_card_votes, 1);
  assert.equal(s.totals.cross_card_votes, 0);
});

test("localStats: mixed same and cross votes split totals correctly", async () => {
  const [a, b] = [art("mx1"), art("mx2")];
  await T.recordVote(matchup("Card X", a, b), a, b);
  const ca = { ...art("mx3"), card: "Card X" };
  const cb = { ...art("mx4"), card: "Card Y" };
  const cross = { format: "modern", mode: "cross", card: "Card X vs Card Y",
                  cards: ["Card X", "Card Y"], total_arts: 2, arts: [ca, cb] };
  await T.recordVote(cross, ca, cb);
  await T.recordVote(cross, ca, cb);
  const s = T.localStats("all");
  assert.equal(s.totals.votes, 3);
  assert.equal(s.totals.same_card_votes, 1);
  assert.equal(s.totals.cross_card_votes, 2);
});

test("localStats: format filter excludes other formats", async () => {
  const [a, b] = [art("a3"), art("b3")];
  await T.recordVote(matchup("Card M", a, b, "modern"), a, b);
  await T.recordVote(matchup("Card L", a, b, "legacy"), a, b);
  const s = T.localStats("legacy");
  assert.equal(s.totals.votes, 1);
  assert.deepEqual(s.totals.by_format, { legacy: 1 });
});

test("localCard: aggregates arts and head-to-head for one card only", async () => {
  const [a, b, c] = [art("cx1", "Alice"), art("cx2", "Bob"), art("cx3", "Cara")];
  const foo = matchup("Foo", a, b);
  const bar = matchup("Bar", c, c);       // won't happen — sanity
  await T.recordVote(foo, a, b);
  await T.recordVote(foo, a, b);
  await T.recordVote(foo, b, a);
  await T.recordVote(matchup("Baz", a, c), a, c);   // different card, ignored
  const s = T.localCard("Foo");
  assert.equal(s.card_name, "Foo");
  assert.equal(s.totals.votes, 3);
  assert.equal(s.totals.arts, 2);
  assert.equal(s.arts[0].id, "cx1");        // 2-1 beats 1-2
  assert.equal(s.arts[0].wins, 2);
  assert.equal(s.pairs.length, 1);
  assert.equal(s.pairs[0].n, 3);
});

test("localCard: cross-card votes contribute to both cards' totals", async () => {
  const wa = { ...art("cc1", "Alice"), card: "Alpha" };
  const wb = { ...art("cc2", "Bob"), card: "Beta" };
  const m = { format: "modern", mode: "cross", card: "Alpha vs Beta",
              cards: ["Alpha", "Beta"], total_arts: 2, arts: [wa, wb] };
  await T.recordVote(m, wa, wb);
  const alpha = T.localCard("Alpha"), beta = T.localCard("Beta");
  assert.equal(alpha.totals.cross_card_votes, 1);
  assert.equal(beta.totals.cross_card_votes, 1);
  assert.equal(alpha.pairs.length, 0, "cross-card duels don't count as head-to-head");
  assert.equal(alpha.arts.find(x => x.id === "cc1").wins, 1);
  assert.equal(beta.arts.find(x => x.id === "cc2").losses, 1);
});

test("localArtist: ranks their arts and sums a career record", async () => {
  const alice1 = art("a1", "Alice"), alice2 = art("a2", "Alice");
  const bob = art("bb", "Bob");
  await T.recordVote(matchup("One", alice1, bob), alice1, bob);
  await T.recordVote(matchup("One", alice1, bob), alice1, bob);
  await T.recordVote(matchup("Two", alice2, bob), bob, alice2);   // Alice loses
  const s = T.localArtist("Alice");
  assert.equal(s.artist, "Alice");
  assert.equal(s.totals.arts, 2);
  assert.equal(s.totals.wins, 2);
  assert.equal(s.totals.losses, 1);
  assert.equal(s.totals.win_rate, 67);
  assert.equal(s.arts[0].id, "a1");   // 2-0 outranks 0-1
  assert.equal(s.arts[0].card_name, "One");
});

test("localStats: blowouts need >=65% and closest prefers even splits", async () => {
  const [a, b] = [art("a4"), art("b4")];
  const even = matchup("Even Card", a, b);
  await T.recordVote(even, a, b);
  await T.recordVote(even, b, a);
  await T.recordVote(even, a, b);
  await T.recordVote(even, b, a);          // 2-2 split
  const [c, d] = [art("c4"), art("d4")];
  const sweep = matchup("Sweep Card", c, d);
  for (let i = 0; i < 4; i++) await T.recordVote(sweep, c, d);  // 4-0
  const s = T.localStats("all");
  assert.equal(s.closest[0].card_name, "Even Card");
  assert.equal(s.blowouts.length, 1);
  assert.equal(s.blowouts[0].card_name, "Sweep Card");
});
