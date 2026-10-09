/* Bounded token matching with memoized failures; no user-supplied regular expressions. */
(() => {
    const labels = { any: "若干字符", digits: "数字", literal: "特定字符", separator: "分隔符", season: "季数", episode: "集数", letters: "文字", space: "空格" };
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
                // Numbers are indivisible: S01E020 must never silently become E02.
                if (["season", "episode", "digits"].includes(block.type)) ends = end > offset ? [end] : [];
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
    const api = { labels, presets, parse };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else window.FilenameRules = api;
})();
