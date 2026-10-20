// MovieDB 云端入口：本地在线时原样转发到 Tunnel；离线时提供只读快照。
//
// - library.yu2yu2.dpdns.org/*（Cloudflare Access 保护）：先请求源站（Tunnel → 本机 Flask）。
//   本机响应带 X-MovieDB-Origin 标记；没有标记的 5xx 视为离线（如 Tunnel 1033/530）。
// - 离线：GET 页面读取 R2 中预渲染的 HTML，缺失时交给 Python 渲染 Worker；
//   /static/* 读取 R2；写操作返回 503。
// - *.workers.dev：仅开放 /_sync/*，供本机同步脚本凭令牌上传快照；带令牌的其他请求返回快照。
// - SNAPSHOT_HOSTS 中的预览域名：始终返回快照。

const OFFLINE_TTL_MS = 15_000;
const META_TTL_MS = 30_000;
const READ_ONLY_MESSAGE = "本地 MovieDB 未连接，当前为只读快照。";
const SYNC_PREFIXES = ["page/", "static/", "data/library.db", "deploy/"];

let offlineUntil = 0;
let metaCache = { value: null, at: 0 };

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        if (url.pathname.startsWith("/_sync/")) {
            return handleSync(request, env, url);
        }
        if (url.hostname.endsWith(".workers.dev")) {
            // 凭同步令牌可直接查看快照，用于部署后自检。
            return (await authorized(request, env))
                ? serveSnapshot(request, env, url)
                : new Response("Not found", { status: 404 });
        }
        if ((env.SNAPSHOT_HOSTS || "").split(",").includes(url.hostname)) {
            // 预览域名（同样受 Access 保护）始终显示快照，便于本机在线时检查离线效果。
            return serveSnapshot(request, env, url);
        }
        if (Date.now() >= offlineUntil) {
            const response = await fetchOrigin(request, env);
            if (response) {
                return response;
            }
            offlineUntil = Date.now() + OFFLINE_TTL_MS;
        }
        return serveSnapshot(request, env, url);
    },
};

async function fetchOrigin(request, env) {
    try {
        // 生产环境直接请求同名源站（DNS 指向 Tunnel）；LOCAL_ORIGIN 仅用于本地测试。
        const target = env.LOCAL_ORIGIN
            ? new Request(env.LOCAL_ORIGIN + new URL(request.url).pathname + new URL(request.url).search, request)
            : request;
        const response = await fetch(target);
        if (response.headers.has("X-MovieDB-Origin") || response.status < 500) {
            return response;
        }
    } catch {
        // 网络错误同样视为离线。
    }
    return null;
}

// 与 scripts/publish_snapshot.py 的 page_key 保持一致。
export function pageKey(url) {
    const pairs = [...url.searchParams.entries()]
        .filter(([, value]) => value !== "")
        .sort(([a, x], [b, y]) => (a < b ? -1 : a > b ? 1 : x < y ? -1 : x > y ? 1 : 0));
    const query = pairs.map(([name, value]) => `${encode(name)}=${encode(value)}`).join("&");
    return `page${url.pathname}${query ? `?${query}` : ""}`;
}

function encode(value) {
    return encodeURIComponent(value).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

async function getMeta(env) {
    if (metaCache.value && Date.now() - metaCache.at < META_TTL_MS) {
        return metaCache.value;
    }
    const object = await env.SNAPSHOT.get("data/meta.json");
    const value = object ? await object.json() : null;
    metaCache = { value, at: Date.now() };
    return value;
}

async function serveSnapshot(request, env, url) {
    if (!["GET", "HEAD"].includes(request.method)) {
        return readOnlyResponse(request, 503);
    }
    if (url.pathname.startsWith("/static/")) {
        return serveObject(env, `static/${decodeURIComponent(url.pathname.slice(8))}`, "public, max-age=300");
    }
    const meta = await getMeta(env);
    if (!meta) {
        return htmlMessage("只读快照尚未同步。请在电脑上启动 MovieDB，完成首次同步后再访问。", 503);
    }
    const page = await env.SNAPSHOT.get(pageKey(url));
    if (page) {
        return new Response(request.method === "HEAD" ? null : page.body, {
            headers: {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "X-MovieDB-Origin": "snapshot",
            },
        });
    }
    try {
        const headers = new Headers(request.headers);
        headers.set("X-Snapshot-Version", meta.version);
        const rendered = await env.RENDERER.fetch(new Request(request.url, { method: request.method, headers, redirect: "manual" }));
        const response = new Response(rendered.body, rendered);
        response.headers.set("Cache-Control", "no-store");
        return response;
    } catch {
        return htmlMessage("只读快照暂时无法渲染此页面，请返回媒体库或稍后重试。", 503);
    }
}

async function serveObject(env, key, cacheControl) {
    const object = await env.SNAPSHOT.get(key);
    if (!object) {
        return new Response("Not found", { status: 404 });
    }
    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    headers.set("Cache-Control", cacheControl);
    return new Response(object.body, { headers });
}

function readOnlyResponse(request, status) {
    const accept = request.headers.get("Accept") || "";
    if (accept.includes("text/html")) {
        return htmlMessage(READ_ONLY_MESSAGE + "修改、录入与播放需要电脑上的 MovieDB 和 Tunnel 在线。", status);
    }
    return Response.json({ error: READ_ONLY_MESSAGE, read_only: true }, {
        status,
        headers: { "X-MovieDB-Origin": "snapshot" },
    });
}

function htmlMessage(message, status) {
    const body = `<!DOCTYPE html><html lang="zh-CN" data-bs-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="initial-scale=1, width=device-width"><title>Media</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="d-flex align-items-center justify-content-center min-vh-100"><div class="text-center p-4">
<p class="fs-5 text-body-secondary">${message}</p><a class="btn btn-sm btn-outline-secondary" href="/">返回媒体库</a></div></body></html>`;
    return new Response(body, {
        status,
        headers: { "Content-Type": "text/html; charset=utf-8", "X-MovieDB-Origin": "snapshot" },
    });
}

async function authorized(request, env) {
    const header = request.headers.get("Authorization") || "";
    if (!header.startsWith("Bearer ") || !env.SYNC_TOKEN_SHA256) {
        return false;
    }
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(header.slice(7).trim()));
    const actual = new Uint8Array(digest);
    const expected = new Uint8Array(env.SYNC_TOKEN_SHA256.match(/../g).map((byte) => parseInt(byte, 16)));
    return actual.length === expected.length && crypto.subtle.timingSafeEqual(actual, expected);
}

async function handleSync(request, env, url) {
    if (!(await authorized(request, env))) {
        return Response.json({ error: "unauthorized" }, { status: 401 });
    }
    const route = `${request.method} ${url.pathname}`;
    if (route === "GET /_sync/manifest") {
        const object = await env.SNAPSHOT.get("data/manifest.json");
        return Response.json({ manifest: object ? await object.json() : {} });
    }
    if (route === "PUT /_sync/object") {
        const key = decodeURIComponent(request.headers.get("X-Object-Key") || "");
        if (!SYNC_PREFIXES.some((prefix) => key.startsWith(prefix)) || key.includes("..")) {
            return Response.json({ error: "invalid key" }, { status: 400 });
        }
        const options = {
            httpMetadata: { contentType: request.headers.get("Content-Type") || "application/octet-stream" },
        };
        const sha256 = request.headers.get("X-Object-Sha256");
        if (sha256) {
            options.sha256 = sha256;
        }
        await env.SNAPSHOT.put(key, request.body, options);
        return Response.json({ ok: true });
    }
    if (route === "POST /_sync/delete") {
        const { keys = [] } = await request.json();
        const allowed = keys.filter((key) => SYNC_PREFIXES.some((prefix) => key.startsWith(prefix)));
        for (let start = 0; start < allowed.length; start += 1000) {
            await env.SNAPSHOT.delete(allowed.slice(start, start + 1000));
        }
        return Response.json({ ok: true, deleted: allowed.length });
    }
    if (route === "POST /_sync/commit") {
        const { manifest, meta } = await request.json();
        if (!manifest || !meta?.version) {
            return Response.json({ error: "invalid commit" }, { status: 400 });
        }
        await env.SNAPSHOT.put("data/manifest.json", JSON.stringify(manifest), {
            httpMetadata: { contentType: "application/json" },
        });
        await env.SNAPSHOT.put("data/meta.json", JSON.stringify({ ...meta, committed_at: new Date().toISOString() }), {
            httpMetadata: { contentType: "application/json" },
        });
        metaCache = { value: null, at: 0 };
        return Response.json({ ok: true, version: meta.version });
    }
    return Response.json({ error: "not found" }, { status: 404 });
}
