/* Bounded token matching with memoized failures; no user-supplied regular expressions. */
(() => {
    const labels = { any: "任意内容", digits: "数字", literal: "固定文字", separator: "分隔符", season: "季号", episode: "集号", letters: "字母或汉字", space: "空格" };
    const b = (type, value = "") => ({ type, value });
    const presets = [
        { name: "S01E02", blocks: [b("any"), b("literal", "S"), b("season"), b("literal", "E"), b("episode"), b("any")] },
        { name: "1x02", blocks: [b("any"), b("season"), b("literal", "x"), b("episode"), b("any")] },
        { name: "第1季第02集", blocks: [b("any"), b("literal", "第"), b("season"), b("literal", "季第"), b("episode"), b("literal", "集"), b("any")] },
        { name: "EP02", blocks: [b("any"), b("literal", "EP"), b("episode"), b("any")] },
        { name: "[02]", blocks: [b("any"), b("literal", "["), b("episode"), b("literal", "]"), b("any")] },
        { name: "标题 - 02", blocks: [b("any"), b("literal", " - "), b("episode"), b("any")] },
        { name: "第02集", blocks: [b("any"), b("literal", "第"), b("episode"), b("literal", "集"), b("any")] },
    ];
    function parse(name, blocks) {
        if (name.length > 512 || blocks.length > 16 || !blocks.length) return null;
        if (blocks.filter(x => x.type === "episode").length !== 1 || blocks.filter(x => x.type === "season").length > 1) return null;
        const failed = new Set();
        function match(index, offset) {
            if (index === blocks.length) return offset === name.length ? [] : null;
            const key = `${index}:${offset}`;
            if (failed.has(key)) return null;
            const block = blocks[index];
            let ends = [];
            if (block.type === "literal") {
                if (block.value && block.value.length <= 64 && name.slice(offset, offset + block.value.length).toLowerCase() === block.value.toLowerCase()) ends = [offset + block.value.length];
            } else if (block.type === "any") {
                ends = Array.from({ length: name.length - offset + 1 }, (_, i) => offset + i);
            } else {
                const pattern = { digits: /[0-9]/, season: /[0-9]/, episode: /[0-9]/, separator: /[ ._\-]/, space: /\s/, letters: /\p{L}/u }[block.type];
                if (!pattern) return null;
                let end = offset;
                while (end < name.length && pattern.test(name[end])) end++;
                // Numbers are indivisible in both directions: S01E020 never becomes E02, and "any" cannot eat half of 12.
                if (["season", "episode", "digits"].includes(block.type)) ends = end > offset && !(offset > 0 && /[0-9]/.test(name[offset - 1])) ? [end] : [];
                else for (let at = end; at > offset; at--) ends.push(at);
            }
            for (const end of ends) {
                const rest = match(index + 1, end);
                if (rest !== null) return [{ type: block.type, start: offset, end, text: name.slice(offset, end) }, ...rest];
            }
            failed.add(key);
            return null;
        }
        const spans = match(0, 0);
        if (!spans) return null;
        const result = { spans };
        for (const span of spans) if (["season", "episode"].includes(span.type)) result[span.type] = Number(span.text);
        return result;
    }

    // ---- 以例代规：在样例文件名上标出季、集，反推匹配块 ----------------------
    const kind = ch => /\s/.test(ch) ? "space" : /[A-Za-z]/.test(ch) ? "latin" : /\p{L}/u.test(ch) ? "cjk" : "punct";
    const RUN_LIMIT = { latin: 8, cjk: 1, punct: 3, space: 3 };
    function runBefore(text, end) {
        if (end <= 0) return end;
        const type = kind(text[end - 1]);
        let start = end;
        while (start > 0 && kind(text[start - 1]) === type && end - start < RUN_LIMIT[type]) start--;
        return start;
    }
    // 标记前面紧挨着的一小段作为定位文字，例如 S、E、EP、第、" - "、"["。
    function contextBefore(text) {
        let start = runBefore(text, text.length);
        if (start > 0 && kind(text[start]) === "space") start = runBefore(text, start);
        return text.slice(start);
    }
    const CLOSING = /^[集话話期回季部\]\)）】」』]/;
    function buildRule(name, marks) {
        const sorted = [...marks].sort((a, b) => a.start - b.start);
        if (sorted.filter(x => x.type === "episode").length !== 1 || sorted.filter(x => x.type === "season").length > 1) return null;
        const out = [];
        let cursor = 0;
        sorted.forEach((mark, index) => {
            const between = name.slice(cursor, mark.start);
            if (index > 0 && between.length && between.length <= 6) out.push(b("literal", between));
            else {
                if (index > 0) {
                    const lead = between.match(CLOSING)?.[0];
                    if (lead) out.push(b("literal", lead));
                }
                const context = contextBefore(between);
                if (context) out.push(b("any"), b("literal", context));
                else if (index > 0) out.push(b("any"));
            }
            out.push(b(mark.type));
            cursor = mark.end;
        });
        const rest = name.slice(cursor), tail = rest.match(CLOSING)?.[0] || "";
        if (tail) out.push(b("literal", tail));
        if (rest.length > tail.length) out.push(b("any"));
        const check = parse(name, out);
        if (!check) return null;
        for (const mark of sorted) {
            const span = check.spans.find(x => x.type === mark.type);
            if (!span || span.start !== mark.start || span.end !== mark.end) return null;
        }
        return out;
    }
    function presetIndex(blocks) {
        const key = JSON.stringify(blocks.map(x => [x.type, x.value || ""]));
        return presets.findIndex(preset => JSON.stringify(preset.blocks.map(x => [x.type, x.value || ""])) === key);
    }

    // ---- 文件夹中的季号：第二季 / Season 2 / S02 ---------------------------
    const DIGIT = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
    function chineseNumber(text) {
        if (/^\d+$/.test(text)) return Number(text);
        let total = 0, current = 0;
        for (const ch of text) {
            if (ch === "十") { total += (current || 1) * 10; current = 0; }
            else if (ch in DIGIT) current = DIGIT[ch];
            else return null;
        }
        return total + current;
    }
    function folderSeason(text) {
        const patterns = [/第\s*([0-9]{1,3}|[零〇一二两三四五六七八九十]{1,3})\s*季/, /season[\s._-]*([0-9]{1,3})/i, /(?<![A-Za-z0-9])S([0-9]{1,2})(?![0-9])(?!\s*E[0-9])/i];
        for (const pattern of patterns) {
            const found = pattern.exec(text || "");
            const season = found && chineseNumber(found[1]);
            if (season != null) return { season, start: found.index, end: found.index + found[0].length };
        }
        return null;
    }

    // ---- 连续编号：各文件夹按顺序接到 TMDB 的季集上 -----------------------------
    // groups: [{ key, season, episodes: [原集号] }]，episodes: TMDB 单集列表。
    function continuePlan(groups, episodes) {
        const order = episodes.filter(x => x.season_number > 0 && x.episode_number != null)
            .sort((a, b) => a.season_number - b.season_number || a.episode_number - b.episode_number);
        const exists = new Set(order.map(x => `${x.season_number}:${x.episode_number}`));
        const ready = groups.filter(group => group.episodes.length);
        if (ready.length < 2) return null;
        ready.sort((a, b) => (a.season ?? 0) - (b.season ?? 0) || a.key.localeCompare(b.key, "zh-CN", { numeric: true }));
        let cursor = 0;
        const plan = [];
        for (const group of ready) {
            const start = order[cursor];
            if (!start) return null;
            const low = Math.min(...group.episodes), high = Math.max(...group.episodes);
            const season = start.season_number, offset = start.episode_number - low;
            if (group.episodes.some(value => !exists.has(`${season}:${value + offset}`))) return null;
            plan.push({ key: group.key, season, offset, first: low + offset, last: high + offset });
            cursor = order.findIndex(x => x.season_number === season && x.episode_number === high + offset) + 1;
        }
        return plan;
    }
    const api = { labels, presets, parse, buildRule, presetIndex, folderSeason, continuePlan };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else window.FilenameRules = api;
})();
