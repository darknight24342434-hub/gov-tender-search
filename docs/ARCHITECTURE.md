# Project.政府標案搜尋 架構圖

生成時間：2026-07-02 22:37

## 架構總覽

```mermaid
flowchart TD
    A[專案根目錄] --> B[README.md]
    A --> C[docs/ARCHITECTURE.md]
    A --> D[docs/說明書.html]
    A --> T1[app/]
    A --> T2[data/]
    A --> T3[deploy/]
    A --> T4[output/]
    A --> T5[reports/]
    A --> T6[scripts/]
    A --> T7[tests/]
    A --> L[主要技術/內容]
    L --> L1[Python: 478 檔]
    L --> L2[PowerShell: 1 檔]
    L --> L3[SQL: 1 檔]
    L --> L4[HTML: 1 檔]
    A --> N[關鍵入口檔]
    N --> N1[requirements.txt]
    N --> N2[README.md]
    N --> N3[.env.example]
    N --> N4[.gitignore]
    N --> N5[run.ps1]
```

## 主要內容

法規、標案或資料蒐整流程。目前偵測到主要內容型態：Python, PowerShell, SQL, HTML。

## 子資料夾

- `app/`
- `data/`
- `deploy/`
- `output/`
- `reports/`
- `scripts/`
- `tests/`

## 技術/檔案型態

- Python: 478 檔
- PowerShell: 1 檔
- SQL: 1 檔
- HTML: 1 檔

## 邊界與風險

- 此文件只根據本機檔案結構與非敏感檔名推斷，不讀取或揭露金鑰、token、session、cookie、`.env` 等敏感資料。
- 自動圖只描述目前可見結構；若專案有外部服務、雲端帳號或手動流程，需由後續人工驗收補充。
