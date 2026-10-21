const assert = require("node:assert/strict");
const { parse, presets, buildRule, presetIndex, folderSeason, continuePlan } = require("../static/filename-rules.js");
assert.equal(parse("Show.S02E013.1080p.mkv", presets[0].blocks).episode, 13);
assert.equal(parse("Show.S00E01.mkv", presets[0].blocks).season, 0);
assert.equal(parse("Show.2x03.mkv", presets[1].blocks).episode, 3);
assert.equal(parse("标题第2季第03集.mkv", presets[2].blocks).season, 2);
assert.equal(parse("标题 [03].mkv", presets[4].blocks).episode, 3);
assert.equal(parse("标题 - 03.mkv", presets[5].blocks).episode, 3);
assert.equal(parse("Movie.2026.1080p.mkv", presets[0].blocks), null);
const custom = [{ type: "literal", value: "[Group] " }, { type: "letters" }, { type: "separator" }, { type: "episode" }, { type: "literal", value: ".mkv" }];
const match = parse("[Group] 标题 - 12.mkv", custom);
assert.equal(match.episode, 12);
assert.equal(match.spans.find(x => x.type === "episode").text, "12");
assert.equal(parse("[Group] 标题 - 12.mkv", [custom[1], custom[0], ...custom.slice(2)]), null);
assert.equal(parse("01.mkv", [{ type: "episode" }, { type: "episode" }, { type: "any" }]), null);
assert.equal(parse("A".repeat(512), [...Array(14)].map(() => ({ type: "any" })).concat({ type: "episode" })), null);
// "any" 不能吃掉半个数字。
assert.equal(parse("12.mkv", [{ type: "any" }, { type: "episode" }, { type: "any" }]).episode, 12);
// 以例代规：标出季、集后反推规则，并能套用到同类文件。
const mark = (name, text, type, from = 0) => { const start = name.indexOf(text, from); return { type, start, end: start + text.length }; };
const sample = "Berlin S01E02 - 2160p.mkv";
const built = buildRule(sample, [mark(sample, "01", "season"), mark(sample, "02", "episode")]);
assert.equal(presetIndex(built), 0);
assert.deepEqual([parse("Berlin S01E07 - 2160p.mkv", built).season, parse("Berlin S01E07 - 2160p.mkv", built).episode], [1, 7]);
const cn = "标题第03集.mp4";
const cnRule = buildRule(cn, [mark(cn, "03", "episode")]);
assert.equal(parse("另一个标题第12集.mkv", cnRule).episode, 12);
const bare = "01.mp4";
assert.equal(parse("12.mp4", buildRule(bare, [mark(bare, "01", "episode")])).episode, 12);
const long = "Show Season 2 Episode 05 1080p.mkv";
const longRule = buildRule(long, [mark(long, "2", "season"), mark(long, "05", "episode")]);
assert.deepEqual([parse("Show Season 3 Episode 11 1080p.mkv", longRule).season, parse("Show Season 3 Episode 11 1080p.mkv", longRule).episode], [3, 11]);
assert.equal(buildRule(sample, [mark(sample, "01", "season")]), null);
// 文件夹季号。
assert.equal(folderSeason("纸钞屋（韩版）第二季 2022").season, 2);
assert.equal(folderSeason("Show Season 03").season, 3);
assert.equal(folderSeason("Show.S02.1080p").season, 2);
assert.equal(folderSeason("第十二季").season, 12);
assert.equal(folderSeason("Show S01E01"), null);
assert.equal(folderSeason("纸钞屋 2022【全6集】"), null);
// 连续编号：第二个文件夹的 1–6 集接到第 1 季第 7–12 集。
const tmdb = Array.from({ length: 12 }, (_, i) => ({ season_number: 1, episode_number: i + 1 }));
const plan = continuePlan([{ key: "b", season: 2, episodes: [1, 2, 3, 4, 5, 6] }, { key: "a", season: 1, episodes: [1, 2, 3, 4, 5, 6] }], tmdb);
assert.deepEqual(plan.map(x => [x.key, x.season, x.offset, x.first, x.last]), [["a", 1, 0, 1, 6], ["b", 1, 6, 7, 12]]);
assert.equal(continuePlan([{ key: "a", season: 1, episodes: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] }, { key: "b", season: 2, episodes: [1, 2, 3, 4, 5] }], tmdb), null);
console.log("Filename rules: presets, custom order, literal escaping, highlights and bounded failure passed.");
