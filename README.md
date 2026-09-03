# Nicokara Studio Next

本项目是本地运行的卡拉 OK 工程编辑器。它以可保存、可回退和可人工精修的工程为核心，支持导入视频和歌词、编辑歌词时间轴与注音、实时预览字幕，并通过 FFmpeg 导出成片。自动分离、人声识别、注音和对齐仅用于生成初稿，最终内容以用户编辑的工程数据为准。

## 运行前准备

请先安装并确保以下工具已加入系统 `PATH`：

- [FFmpeg](https://ffmpeg.org/)
- [Node.js](https://nodejs.org/)

## 启动

1. 双击 `install_python.bat`，自动安装项目所需的 Python 环境及后端依赖。
2. 双击 `start-nicokara.bat` 启动项目。首次启动会自动安装前端依赖。
3. 在浏览器打开 `http://127.0.0.1:5173`。

## 第三方项目声明

本项目参考和引用了以下开源项目：

- [FA-Kara](https://github.com/moriwx/FA-Kara)：用于可选的歌词强制对齐能力，MIT License。
- [Kirakara-Player](https://github.com/FMPeach/Kirakara-Player)：用于字幕排版、预览和渲染相关能力，MIT License。

详细的第三方许可与引用范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
