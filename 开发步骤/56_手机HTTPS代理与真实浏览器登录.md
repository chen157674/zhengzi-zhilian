# 56 手机HTTPS代理与真实浏览器登录

状态（2026-10-25）：临时可信HTTPS入口、Waitress代理信任、真实浏览器登录和主要页面访问已通过；Android/iOS实机摄像头权限、横竖屏、前后镜头、后台/锁屏和10分钟连续使用仍需实际设备回传，不能用桌面浏览器代替。

## 1. 入口方案

本轮不修改Cloudflare DNS、洛杉矶服务器Nginx、防火墙或系统代理。使用项目本地`cloudflared 2026.10.3` Quick Tunnel临时反代本机`127.0.0.1:8770`：

- 官方资产：`cloudflared-windows-amd64.exe`
- 项目路径：`.tools/cloudflared.exe`
- SHA-256：`76b2805253d0d6ffacb092a7acfbee99e7af6917835c0c5410e23f3ef5052da2`
- 运行日志、SQLite、auth-secret和运行状态均在`.tmp/mobile-validation/`
- Quick Tunnel停止后地址失效，不作为正式部署或长期域名

隔离管理员`mobile_validator`使用随机32字符密码，密码只保留在当前会话并交给用户本轮测试，不写入项目文件。正式个人库、已有账号和训练记录均未读取或迁移。

## 2. 实际发现与修复

首次公网请求到达Waitress，但`OriginProxy`返回`SECURE_ORIGIN_REQUIRED`。内存WSGI测试能通过，真实Waitress链失败，定位到两处运行时差异：

1. Windows/Waitress的本机对端表示可能带端口，原代码只接受字面量`127.0.0.1`和`::1`。
2. Waitress 3默认清除未信任的`X-Forwarded-*`，原运行入口没有声明可信代理，因此`X-Forwarded-Proto`在应用安全门前被移除。

修复：

- `mobile_origin.py`新增严格回环地址解析，仅接受IPv4/IPv6 loopback及可选合法端口；非回环、伪主机名、坏端口继续拒绝。
- `OriginProxy`继续要求明确公网Host、回环对端和HTTPS，只允许Waitress已验证后的`wsgi.url_scheme=https`作为协议依据；`/_local/`控制入口仍禁止通过公网Host。
- `runtime.py`仅在配置`MOBILE_PUBLIC_ORIGIN`时设置Waitress：`trusted_proxy=127.0.0.1`、`trusted_proxy_count=1`，只信任`x-forwarded-proto`并清除其余未信任代理头。不信任`X-Forwarded-Host`或`X-Forwarded-For`，不使用通配符。

## 3. 验证

专项测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_mobile_origin
```

4项通过，覆盖IPv4/IPv6回环、带端口回环、非回环、伪造主机、坏端口、组合协议、跨来源写入、Secure Cookie和本机控制入口隔离。

真实运行验证：

- 公网`GET /api/v1/health`返回200。
- 公网`GET /login`返回200。
- ChatGPT内置真实浏览器通过HTTPS完成CSRF登录，进入首页。
- 登录后实际打开`/realtime`、`/video`、`/records`和`/account/ai`。
- 实时页在未点击“开启摄像头”前没有权限弹窗，并显示“点击开启后才会请求摄像头权限”。
- AI设置页正确显示项目密钥未配置、系统体验0次，不伪装为真实AI可用。

命令行CookieJar首次在Quick Tunnel上出现CSRF 403，真实浏览器完成同一登录。步骤58后续定位：HTTPS写请求需要同源Referer，原命令行请求未提供；补同源Referer后同一CookieJar登录200。不能将原403归因于Cookie客户端差异，应用的CSRF检查保持有效。

## 4. 尚待实机

用户已收到逐步测试说明，需至少记录手机型号、系统、浏览器版本，以及首次允许/拒绝重试、前后镜头、横竖屏、后台返回、断网恢复和主要业务页面结果。当前只能确认公网安全来源与真实桌面浏览器业务链，R3-11仍保持`in_progress`。
