# 公開網域 + 密碼存取（Cloudflare Tunnel + Access）

因為用的是 **Codex CLI（ChatGPT 訂閱）**，後端必須跑在這台已登入 Codex 的機器上，
不能丟到無狀態 serverless。對外公開的標準做法是 Cloudflare Tunnel，再疊 Access 當第二道閘。

## 架構

```
瀏覽器 → Cloudflare Access（可選的第二道密碼/Email OTP）
       → bids.yourdomain.com（Cloudflare 邊緣，自動 HTTPS）
       → Cloudflare Tunnel（cloudflared）
       → 127.0.0.1:8000（本機 FastAPI，含內建密碼閘）
```

兩層密碼是刻意的縱深防禦：App 內建密碼閘（APP_PASSWORD）一定有；Cloudflare Access 可選。

## 步驟

### 1. 啟動本機後端
```powershell
.\run.ps1 -BindHost 127.0.0.1 -Port 8000
```
> 部署後記得把 .env 的 `COOKIE_SECURE=true`（因為走 https）。

### 2. 安裝 cloudflared
到 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
下載 Windows 版，或用 winget：
```powershell
winget install --id Cloudflare.cloudflared
```

### 3. 登入並建立 Tunnel
```powershell
cloudflared tunnel login                       # 瀏覽器授權，選一個你在 Cloudflare 的網域（或免費 *.cfargotunnel）
cloudflared tunnel create gov-tender            # 建立 tunnel，會產生憑證 json
cloudflared tunnel route dns gov-tender bids.yourdomain.com   # 綁子網域
```

### 4. 設定 config（`%USERPROFILE%\.cloudflared\config.yml`）
```yaml
tunnel: gov-tender
credentials-file: C:\Users\<你>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: bids.yourdomain.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

### 5. 啟動 Tunnel（可裝成 Windows 服務常駐）
```powershell
cloudflared tunnel run gov-tender
# 或安裝為開機自啟服務：
cloudflared service install
```

### 6.（可選）Cloudflare Access — 平台級密碼閘，預設 deny
1. Cloudflare Dashboard → Zero Trust → Access → Applications → Add an application（Self-hosted）。
2. Application domain 填 `bids.yourdomain.com`。
3. Policy 設 **Service Auth / PIN** 或 **One-time PIN（Email OTP）**，Action = Allow，
   只允許你指定的 Email。其餘預設 deny。
4. 這樣外部要先過 Cloudflare 這關，才會碰到 App 的密碼頁。

## 沒有自己的網域？
- Cloudflare 提供 `*.cfargotunnel.com` 形式的免費 quick tunnel：
  ```powershell
  cloudflared tunnel --url http://127.0.0.1:8000
  ```
  會即時給一個隨機公開網址（適合測試；正式用建議綁自己的子網域 + Access）。

## ToS 提醒
Codex CLI 綁定 ChatGPT 訂閱。把它當成長期對外服務的後端代理前，
建議先確認 OpenAI 服務條款是否允許此用途（個人自用風險較低，對外營運請自行評估）。
