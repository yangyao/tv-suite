# 我的电视 · 订阅版

基于 [lizongying/my-tv](https://github.com/lizongying/my-tv) 的 Android TV 直播客户端改造版。保留遥控器操作，通过 M3U 订阅读取频道，直接播放上游视频。

## 功能

- 启动时自动获取订阅并缓存，获取失败时使用上一次有效列表。
- 根据 M3U 分组展示频道，同组同名频道合并为备用线路。
- 凤凰资讯、中文、香港台独立置顶；切台时向凤秀获取最新播放地址，失败时刷新一次并尝试订阅备用线路。
- 播放失败后有限次尝试备用线路。
- 独立应用 ID `com.yangyao.mytv`，可与原版同时安装。
- 无需旧平台鉴权和原生签名库，不需要 NDK/CMake。

除凤凰卫视专用入口外，其他频道由用户自行配置订阅。公开构建默认使用 `https://example.com/playlist.m3u` 占位地址。

## 本地构建

需要 JDK 17 和 Android SDK 34。

1. 将 `playlist.properties.example` 复制到 `private/local.properties`，填写自己的 M3U 订阅地址。`private/` 已被 Git 忽略。
2. 设置 `JAVA_HOME`、`ANDROID_HOME`，或通过本地 `local.properties` 配置 Android SDK 路径。
3. 执行 `./gradlew testDebugUnitTest assembleDebug`。
4. 安装 `app/build/outputs/apk/debug/app-debug.apk`。

也可以通过 `-PplaylistUrl=https://example.com/playlist.m3u` 指定地址；命令行参数优先于本地文件。公共 CI 会显式使用占位地址。

私人订阅、设备地址、部署配置和个人 APK 请仅保存在 `private/` 中。构建时地址会写入 APK，因此个人 APK 也不应上传到公开仓库或 Release。

## 凤凰卫视

频道列表首个分组为“凤凰卫视”，遥控器数字键 1、2、3 分别对应凤凰资讯、凤凰中文、凤凰香港。电视直接访问凤秀的取源接口及视频 CDN，路由器不代理视频。每次选台或从后台恢复时重新获取地址，不将短期签名写入订阅缓存。

订阅中同名或对应繁体名称的凤凰频道会合并为备用线路；即使订阅暂时无法获取、没有缓存，这三个专用频道仍会显示。实际播放取决于凤秀接口及当地网络可用性，不包含账号、付费授权或 DRM 绕过。

应用版本由 `app/build.gradle` 中的 `versionCode` / `versionName` 显式维护，不依赖 Git 标签或历史。

## 来源与版权

本项目基于上游源码改造，原项目说明和版权声明见 [UPSTREAM_README.md](UPSTREAM_README.md)。上游当前检出版本未包含该说明引用的 LICENSE 文件；本仓库不另行授予覆盖上游代码和素材的开源许可。

本次改造的主要变更包括订阅加载、缓存、频道去重、备用线路、凤凰卫视动态取源及移除运行流程中的原生签名依赖。
