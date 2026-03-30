## 小票食物扫描记录器（本地/公网版）

把小票（图片/PDF）里的食物/商品行扫描出来，提取为结构化数据并保存到本地数据库，支持校对、查询、导出。

### 功能
- **上传小票**：支持 `.jpg/.png/.webp` 与 `.pdf`
- **OCR 识别**：把图片/扫描 PDF 转成文字
- **行项目提取**：尝试识别 `名称 / 数量 / 金额(或单价)`（不同商家格式差异较大，提供手动校对）
- **本地学习小 AI**：从你的校对里学习“缩写/错字 → 标准菜名”，下次自动建议/还原（无需联网）
- **存储**：本地默认 `SQLite`；公网部署使用 `Postgres`（更稳定，支持多用户隔离）
- **校对编辑**：页面表格里修改后再保存
- **导出 CSV**：按时间筛选导出

### 安装
建议使用 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### OCR 依赖（macOS）
本项目默认使用 `Tesseract` 做本地 OCR。

```bash
brew install tesseract
# 可选：安装中文语言包（通常会自带；若缺失再装）
brew install tesseract-lang
```

### 运行
```bash
streamlit run app.py
```

### 公网部署（所有人可用 + 每人独立数据）
推荐：**Streamlit Community Cloud + 托管 Postgres（Supabase/Neon）+ OIDC 登录**。

#### 1) 准备 Postgres
- 在 Supabase 或 Neon 创建数据库，拿到连接串（`postgresql://...`）。
- 在 Streamlit Cloud 的 Secrets 里设置：
  - `DATABASE_URL = "postgresql://..."`

#### 2) 配置登录（OIDC）
本项目使用 Streamlit 的 `st.login` / `st.user` 做登录与用户隔离，需要在 Streamlit Cloud Secrets 配置 OIDC。
- 选择一个提供方：Google 或 GitHub
- 按 Streamlit 官方文档配置 `auth.*` 相关 secrets（client id/secret、issuer、redirect 等）

文档参考：`https://docs.streamlit.io/develop/api-reference/user/st.login`

#### 3) 部署到 Streamlit Community Cloud
- 把项目推到 GitHub
- Streamlit Cloud 选择 repo → Deploy
- 配好 Secrets 后重新部署

#### 4) 本地开发（不配 OIDC 的情况下）
默认允许匿名本地用户 `local`（不做隔离），便于调试。
- 如需强制本地也登录：设置 `ALLOW_LOCAL_ANON=0`

### 使用建议
- **拍照更准**：尽量正对、光线足、别反光；小票边缘尽量完整。
- **先校对再入库**：不同小票格式差异大，自动提取只是“初稿”。

