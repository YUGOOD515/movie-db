# 第三方前端库

下表基础库均为 MIT 许可，原样复制自 npm 发布包，未经修改。本地保存而非走 CDN，离线快照也能直接使用。新增图标素材的来源与处理方式单独记录在下方。

| 文件 | 包 | 版本 | 来源 |
| --- | --- | --- | --- |
| `bootstrap.min.css` | bootstrap | 5.3.8 | https://github.com/twbs/bootstrap |
| `bootstrap.bundle.min.js` | bootstrap | 5.3.8 | 同上 |
| `embla-carousel.umd.js` | embla-carousel | 8.6.0 | https://github.com/davidjerleke/embla-carousel |
| `embla-carousel-autoplay.umd.js` | embla-carousel-autoplay | 8.6.0 | 同上 |
| `embla-carousel-fade.umd.js` | embla-carousel-fade | 8.6.0 | 同上 |
| `vanilla-tilt.min.js` | vanilla-tilt | 1.8.1 | https://github.com/micku7zu/vanilla-tilt.js |
| `motion.min.js` | motion（`dist/motion.js`） | 14.4.0 | https://github.com/motiondivision/motion |
| `blurhash.mjs` | blurhash（`dist/index.mjs`） | 2.0.5 | https://github.com/woltapp/blurhash |

升级：`npm i <包名>@<版本>` 后替换对应文件并更新本表。

## 界面、技术参数与任务图标

- `lucide/sprite.svg`：从 `lucide-static@1.63.0` 按需提取的 91 个图标，覆盖导航、分类、技术参数、设置、关联与任务状态。仅将原 SVG 子元素合并为 symbol sprite，不加载整套图标字体或运行时库。ISC 及上游保留的 MIT 许可证位于 `lucide/LICENSE`。[来源](https://www.npmjs.com/package/lucide-static/v/1.63.0)。
- `nard-badges/`：10 个品牌／特殊格式 PNG，仅用于 Dolby Vision、HDR 系列、DTS 系列、TrueHD、Atmos。来源提交 `1b04c75e79035c1be8fcab0e662d11b329cdc2e1`，仅裁去透明边距，图案与比例未修改。[来源](https://github.com/vowl313/NardBadges/tree/1b04c75e79035c1be8fcab0e662d11b329cdc2e1)。上游未附独立许可证，这些素材不包含在上表的 MIT 声明中。

播放版本横向排列，每列以图标配参数纵向对齐。品牌图案按杜比参考高度做光学校准。杜比 PNG 自带约 70% 不透明度，DTS / HDR 的原图为不透明白色，因此通过 CSS 将它们设为 70%，统一视觉亮度；原文件保持不变。浅色主题反色显示单色图案，不拉伸标识。素材随 Git 跟踪并打包，运行时不访问第三方图标服务。

新增通用图标时，使用 `_ui.html` 的 `icon` 宏或 JavaScript 的 `window.appIcon`，并补入 `scripts/build_icons.py` 的 `ICONS` 清单。运行 `uv run python scripts/build_icons.py` 重建，`--check` 核对生成文件；脚本固定 npm 版本并校验 SHA-512，下载缓存在 `.cache/icons/`。`tests/test_icons.py` 核对模板与视图模型的实际引用。评分平台 Logo 继续使用 `static/icons/ratings/`，Bootstrap CSS、组件 JavaScript 及其 spinner、关闭按钮、表单控件保持原生实现。
