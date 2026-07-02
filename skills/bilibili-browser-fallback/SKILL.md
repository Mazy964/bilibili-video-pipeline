---
name: bilibili-browser-fallback
description: B站收藏夹浏览器兜底 — 当 API 不支持跨夹批量操作时，用浏览器导航+勾选+删除
category: bilibili
---

# B站收藏夹浏览器兜底

## 实际能力边界

浏览器已验证可以：
- **登录**：通过 `browser_console` 注入 SESSDATA/bili_jct/DedeUserID 到 `.bilibili.com` domain
- **查看**：导航到 `space.bilibili.com/11976717/favlist` 可看到完整收藏夹列表和视频内容
- **搜索**：顶部搜索框可用

浏览器**无法**自动化：
- **批量操作弹窗**：`浏览器_click("批量操作")` 后弹出的面板是 JS 动态渲染的，无障碍树抓不到里面的按钮（全选/移动至/删除）
- **移动至**下拉：同上，JS 动态菜单不可见
- **因此浏览器不能用于批量自动化**，只能用于可视化确认结果

## 使用场景

| 场景 | 用 API | 用浏览器 |
|---|---|---|
| 新建/删除文件夹 | ✅ folder/add, folder/del | ❌ |
| 移动视频 | ✅ resource/move | ❌ |
| 批量删除 | ✅ 临时夹策略 | ❌ |
| 搜索 UP主 | ✅ keyword 全局搜索 | ✅ 可视化验证 |
| 确认清理结果 | ❌ 只能查数量 | ✅ 眼见为实 |
| 编辑文件夹名 | ✅ folder/edit | ❌ |

## 浏览器验证流程（登录 + 查看）

```
# 1. 导航到收藏夹
browser_navigate("https://space.bilibili.com/11976717/favlist")

# 2. 注入 Cookie（从 ~/.hermes/bilibili_cookies.txt 读）
browser_console("document.cookie = 'SESSDATA=...; domain=.bilibili.com; path=/'")
browser_console("document.cookie = 'bili_jct=...; domain=.bilibili.com; path=/'")
browser_console("document.cookie = 'DedeUserID=11976717; domain=.bilibili.com; path=/'")

# 3. 重新导航加载
browser_navigate("https://space.bilibili.com/11976717/favlist")

# 4. 查看结果
browser_console("document.querySelectorAll('a[href*=\"favlist\"]').length")
```

> 不需要 camofox 或 browser-use —— B站 API 足够强大，临时夹策略完美绕过所有限流。
> API 做不到的事（改文件夹名/封面）暂不需要自动化。
