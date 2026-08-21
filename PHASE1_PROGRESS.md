# Nicokara Studio Next Phase 1 进度日志

## 2026-08-21

### 已完成

- 确认当前工作区根目录作为新项目根目录；没有创建 `nicokara-studio-next` 子目录。
- 保留 `nicokara-studio/` 作为旧版参考实现。
- 新增根目录 `backend/`：FastAPI 应用、SQLite WAL 数据层、工程 JSON/revision、项目删除、设置持久化。
- 新增项目 API：项目列表、创建、打开、重命名/编辑基本信息、永久删除、文档读取与乐观并发保存。
- 新增视频 API：按项目隔离保存 MP4，限制文件大小，尝试 FFprobe 元数据探测和 FFmpeg 缩略图，提供视频/缩略图读取接口。
- 新增根目录 `frontend/`：React + Vite + TypeScript 单页应用。
- 新增 Material 风格项目列表、空白编辑器、视频上传入口、工程基本信息自动保存、独立设置页骨架及全局设置保存。
- 首页与编辑器均提供右下角设置入口，设置页支持 `returnTo` 返回来源页面。
- 后端源码通过 `C:\Users\littlefoxtail\Desktop\software\nicokara\python\python.exe -m compileall` 语法检查。
- 修复 Vite 依赖树：将 `vite` 与 `@vitejs/plugin-react` 固定为兼容的 Vite 8 版本，并补充 React TypeScript 类型包。
- 前端依赖安装成功，`npm audit` 报告 0 个漏洞，`npm run build` 生产构建通过。
- 视频选择改为立即上传：使用浏览器上传进度事件显示百分比、可取消上传、失败提示和替换视频入口。
- 新增根目录 `start-nicokara.bat`，一键启动后端和前端，并固定使用仓库内 `python\\python.exe`。
- 首页更多操作菜单已接通：支持项目重命名和永久删除，删除前显示二次确认；删除会同时清理数据库 revision 和项目媒体目录。
- 设置入口统一为右下角 FAB；设置页隐藏 FAB，并保留单一返回入口，避免设置路由循环嵌套。
- 首页存储说明已明确：工程文档、项目索引、revision 和全局设置写入 SQLite；视频原文件和缩略图写入 `storage/projects/<project_id>/`，不是把二进制视频塞入数据库。
- 缩略图显示已接通：首页项目卡片使用 `/api/projects/{id}/thumbnail`，编辑器视频使用同一地址作为 `poster`；FFmpeg 生成失败时会从第 1 秒回退到第 0 秒，并在媒体元数据记录 `thumbnail_generated`。

前端依赖已于本次开发中安装完成，后续无需重复安装，除非 `package.json` 有变化。

如果以后把仓库上传 GitHub，`pip install -e .` 产生的本地 editable 安装记录不会被提交；只要不把虚拟环境、`*.egg-info` 或 `storage/` 加入 Git，就不会影响其他位置部署。其他机器仍需按 `backend/pyproject.toml` 重新安装依赖。

可选但建议安装并加入 PATH：`ffmpeg` / `ffprobe`。未安装时工程仍可保存视频，但媒体元数据显示为待探测，缩略图不会生成。

### 验证状态

- 后端语法检查：通过。
- 后端 pytest：通过。
- 前端构建：通过（Vite 8，1565 个模块）。

### 下一步

- 安装依赖后运行后端 API 与前端开发服务器，完成浏览器端 Phase 1 验收。
- 补充项目复制、视频拖放、自动保存定时器和项目列表操作菜单。
- 进入 Phase 2：纯文本/LRC/KRL 检测、歌词导入和基础时间轴。
