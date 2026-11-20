// AI 平台预设：设置页与首次引导共用。
// 选平台时填好接口地址和默认模型；有 API Key 时向平台拉取模型列表，作为模型输入框的候选，
// 并换成该平台当前最新的默认模型（规则见 services/setup_wizard.py 的 LLM_PRESETS）。
// 用户自己改过的模型不会被覆盖。
(() => {
    const DEEPSEEK = /\/\/api\.deepseek\.com(\/v1)?\/?$/;

    function attach({ presets, select, base, key, model, keyLink, url, hasSavedKey = () => false, onChange = () => {}, onStatus = () => {}, onPreset = () => {} }) {
        select.replaceChildren(...presets.map(preset => new Option(preset.name, preset.id)), new Option("其他（自定义）", ""));
        const list = document.createElement("datalist");
        list.id = `${model.id}-models`;
        model.after(list);
        model.setAttribute("list", list.id);
        model.autocomplete = "off";
        // 上一次自动填入的模型：模型框仍是它（或为空）时才允许自动替换
        let auto = null, request = 0;
        const current = () => presets.find(item => item.id === select.value) || null;
        const replaceable = () => !model.value.trim() || model.value.trim() === auto;
        const setModel = value => {
            if (!value || !replaceable()) return;
            model.value = value;
            auto = value;
            onChange();
        };

        // replace=false 只更新候选列表（打开页面时），不替换已保存的模型
        async function fetchModels(replace = true) {
            const id = ++request;
            if (!base.value.trim() || !(key.value.trim() || hasSavedKey())) { list.replaceChildren(); onStatus(""); return; }
            onStatus("正在获取模型列表…");
            let result;
            try {
                const response = await fetch(url, {
                    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
                    body: JSON.stringify({ LLM_BASE_URL: base.value.trim(), LLM_API_KEY: key.value.trim() }),
                });
                result = await response.json();
            } catch {
                result = { ok: false, message: "没有获取到模型列表", models: [] };
            }
            if (id !== request) return;
            list.replaceChildren(...(result.models || []).map(name => new Option(name)));
            if (replace && current()) setModel(result.default);
            onStatus(result.ok ? `已获取 ${result.models.length} 个模型，可直接选择` : `${result.message || "没有获取到模型列表"}，可手动填写模型名`, result.ok);
        }

        function apply(fill) {
            const preset = current();
            if (keyLink) {
                keyLink.hidden = !preset;
                if (preset) keyLink.href = preset.key_url;
            }
            model.placeholder = preset ? preset.model : "";
            onPreset(preset);
            if (fill && preset) {
                base.value = preset.base_url;
                setModel(preset.model);
                onChange();
            }
            if (fill) fetchModels();
        }

        // 平台下拉跟随接口地址（页面载入、放弃修改后重新对上）
        function matchBase() {
            const match = presets.find(preset => preset.base_url === base.value.trim() || (preset.id === "deepseek" && DEEPSEEK.test(base.value)));
            select.value = match ? match.id : base.value.trim() ? "" : presets[0].id;
            window.syncSelect?.(select);
            // 已保存的模型就是预设默认值时，视同自动填入：拉到更新的版本可以替换
            auto = match && model.value.trim() === match.model ? match.model : null;
        }
        matchBase();
        select.addEventListener("change", () => apply(true));
        key.addEventListener("change", () => fetchModels());
        model.addEventListener("input", () => { if (model.value.trim() !== auto) auto = null; });
        return {
            // 开启 AI 或进入引导步骤时调用：还没有接口地址就按当前平台填好，然后拉取模型
            activate() { matchBase(); if (base.value.trim()) { apply(false); fetchModels(false); } else apply(true); },
            // 只把下拉、获取链接对上当前的接口地址，不发请求（AI 关闭时载入或放弃修改）
            sync() { matchBase(); apply(false); },
            preset: current,
        };
    }

    window.AiPresets = { attach };
})();
