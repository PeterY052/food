from __future__ import annotations

import os
import re
from datetime import timedelta
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from receipt_scanner.ocr import ocr_image_bytes, ocr_pdf_bytes
from receipt_scanner.parser import items_to_rows, parse_receipt_text
from receipt_scanner.learn import learn_from_edit, suggest_canonical
from receipt_scanner.store import (
    insert_items,
    insert_receipt,
    list_aliases,
    list_receipts,
    query_items,
    stats_items,
    top_items,
    top_items_filtered,
    upsert_alias,
)
from receipt_scanner.websearch import ddg_search


st.set_page_config(page_title="小票食物扫描记录器", layout="wide")

DB_DSN = os.getenv("DATABASE_URL", "data/receipts.db")


def _require_login_and_get_user_id() -> str:
    # Streamlit Cloud 推荐：使用 st.login + st.user（OIDC）
    # 本地开发：使用 SQLite（非 postgres dsn）时默认允许匿名 local，避免本地必须配 OIDC
    allow_local = os.getenv("ALLOW_LOCAL_ANON", "1") == "1"
    is_postgres = DB_DSN.startswith("postgres://") or DB_DSN.startswith("postgresql://")
    if allow_local and not is_postgres:
        return "local"

    try:
        u = st.user  # type: ignore[attr-defined]
        is_logged_in = bool(getattr(u, "is_logged_in", False))
        if not is_logged_in:
            st.info("请先登录后再使用。")
            st.login()  # type: ignore[attr-defined]
            st.stop()

        # st.user 是 dict-like
        data = dict(u)  # type: ignore[arg-type]
        user_id = (data.get("sub") or data.get("email") or "").strip()
        if not user_id:
            st.error("登录成功但未拿到稳定 user_id（缺少 sub/email）。请检查 OIDC 配置。")
            st.stop()
        return user_id
    except Exception:
        st.error("当前运行环境不支持 `st.login/st.user`。公网部署请使用 Streamlit Community Cloud 并配置 OIDC。")
        st.stop()


_FALLBACK_SKIP = [
    "合计",
    "总计",
    "应付",
    "实付",
    "找零",
    "优惠",
    "折扣",
    "会员",
    "税",
    "发票",
    "收银",
    "桌",
    "单号",
    "时间",
    "日期",
    "电话",
    "地址",
    "欢迎",
    "支付",
    "卡",
    "现金",
]


def _fallback_rows_from_ocr(text: str) -> pd.DataFrame:
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if any(k in s for k in _FALLBACK_SKIP):
            continue
        # 去掉明显无意义的短行
        if len(s) < 2:
            continue
        lines.append({"include": True, "name": s, "qty": None, "unit_price": None, "amount": None, "raw": raw})
    return pd.DataFrame(lines)


def _dt_range(from_d: date | None, to_d: date | None) -> tuple[str | None, str | None]:
    if not from_d:
        from_s = None
    else:
        from_s = datetime.combine(from_d, time.min).isoformat(timespec="seconds")
    if not to_d:
        to_s = None
    else:
        to_s = datetime.combine(to_d, time.max).isoformat(timespec="seconds")
    return from_s, to_s


def _nl_time_range(text: str) -> tuple[str | None, str | None, str]:
    t = (text or "").strip()
    today = date.today()

    # 最近 N 天 / 最近一周
    m = re.search(r"最近\s*(\d{1,3})\s*天", t)
    if m:
        n = max(1, min(int(m.group(1)), 365))
        frm = datetime.combine(today - timedelta(days=n - 1), time.min).isoformat(timespec="seconds")
        to = datetime.combine(today, time.max).isoformat(timespec="seconds")
        return frm, to, f"最近{n}天"
    if "最近一周" in t or "近一周" in t:
        frm = datetime.combine(today - timedelta(days=6), time.min).isoformat(timespec="seconds")
        to = datetime.combine(today, time.max).isoformat(timespec="seconds")
        return frm, to, "最近7天"

    # 本周/上周（按周一为起点）
    if "本周" in t or "这周" in t:
        start = today - timedelta(days=today.weekday())
        frm = datetime.combine(start, time.min).isoformat(timespec="seconds")
        to = datetime.combine(today, time.max).isoformat(timespec="seconds")
        return frm, to, "本周"
    if "上周" in t:
        start_this = today - timedelta(days=today.weekday())
        start = start_this - timedelta(days=7)
        end = start_this - timedelta(days=1)
        frm = datetime.combine(start, time.min).isoformat(timespec="seconds")
        to = datetime.combine(end, time.max).isoformat(timespec="seconds")
        return frm, to, "上周"

    # 本月/上月
    if "本月" in t or "这个月" in t:
        start = today.replace(day=1)
        frm = datetime.combine(start, time.min).isoformat(timespec="seconds")
        to = datetime.combine(today, time.max).isoformat(timespec="seconds")
        return frm, to, "本月"
    if "上月" in t:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        frm = datetime.combine(first_prev, time.min).isoformat(timespec="seconds")
        to = datetime.combine(last_prev, time.max).isoformat(timespec="seconds")
        return frm, to, "上月"

    # 默认：不限定
    return None, None, "全部时间"


st.title("小票食物扫描记录器（本地）")

user_id = _require_login_and_get_user_id()

with st.sidebar:
    st.subheader("对话助手（本地）")
    st.caption(f"已登录：`{user_id}`")
    st.caption("用来查询数据库、查看本地学习、手动教别名。完全本地，不联网。")
    allow_web = st.checkbox("允许联网搜索（把你的问题发到搜索引擎）", value=False)
    with st.expander("你可以这样问", expanded=False):
        st.markdown(
            "- `top 20`\n"
            "- `查 菜名 关键词`\n"
            "- `教 别名 -> 标准名`（例如：`教 NUT CO -> 坚果曲奇`）\n"
            "- `建议 别名`（例如：`建议 NUT CO`）\n"
            "- `看别名`\n"
            "- `搜 关键词`（需要开启“允许联网搜索”）\n"
            "- `最近7天买了哪些菜` / `上周都买了什么`\n"
            "- `上月拿铁花了多少` / `本周拿铁买了几次`\n"
        )

    if "chat_msgs" not in st.session_state:
        st.session_state["chat_msgs"] = [
            {
                "role": "assistant",
                "content": "我在。你可以输入：`top 20`、`查 拿铁`、`教 NUT CO -> 坚果曲奇`、`建议 NUT CO`、`看别名`。",
            }
        ]

    for m in st.session_state["chat_msgs"]:
        with st.chat_message(m["role"]):
            st.write(m["content"])

    user_text = st.chat_input("输入指令或问题")
    if user_text:
        st.session_state["chat_msgs"].append({"role": "user", "content": user_text})

        def _reply(text: str) -> None:
            st.session_state["chat_msgs"].append({"role": "assistant", "content": text})

        t = user_text.strip()
        low = t.lower()
        from_s, to_s, range_label = _nl_time_range(t)

        # 教别名：教 A -> B
        m = re.match(r"^\s*教\s+(.+?)\s*(?:->|=>|＝>|→)\s*(.+?)\s*$", t)
        if m:
            raw, canon = m.group(1).strip(), m.group(2).strip()
            upsert_alias(DB_DSN, user_id=user_id, raw_name=raw, canonical_name=canon)
            _reply(f"已记住：`{raw}` → **{canon}**。下次会自动给建议/命中时自动替换。")
        elif low.startswith("看别名") or low.startswith("别名"):
            rows = list_aliases(DB_DSN, user_id=user_id, limit=30)
            if not rows:
                _reply("目前还没有学到别名。你可以在表格里改名并保存，或输入：`教 缩写 -> 标准名`。")
            else:
                lines = [f"- `{r['raw_name']}` → **{r['canonical_name']}**（{r['cnt']} 次）" for r in rows]
                _reply("最近学到的别名：\n" + "\n".join(lines))
        elif low.startswith("top"):
            mm = re.search(r"(\d+)", low)
            n = int(mm.group(1)) if mm else 20
            n = max(1, min(n, 100))
            rows = top_items(DB_DSN, user_id=user_id, limit=n)
            if not rows:
                _reply("数据库里还没有行项目数据。先入库几张小票再试。")
            else:
                lines = [f"- **{r['name']}**：{r['cnt']} 次，累计金额 {r['total_amount']}" for r in rows]
                _reply(f"出现次数 Top {n}：\n" + "\n".join(lines))
        elif low.startswith("建议"):
            q = t.replace("建议", "", 1).strip()
            s = suggest_canonical(DB_DSN, user_id=user_id, raw_name=q)
            if not s:
                _reply("我还没学到这个缩写/相似写法。你可以用：`教 缩写 -> 标准名` 先教我一次。")
            else:
                _reply(f"我建议把 `{q}` 还原成 **{s.canonical}**（相似度 {s.score}%）。")
        elif low.startswith("查"):
            q = t.replace("查", "", 1).strip()
            if not q:
                _reply("你要查什么？例如：`查 拿铁`。")
            else:
                rows = query_items(DB_DSN, user_id=user_id, name_like=q, limit=20)
                if not rows:
                    _reply(f"没查到包含 **{q}** 的记录。")
                else:
                    lines = []
                    for r in rows:
                        when = r.get("created_at", "")
                        amt = r.get("amount", None)
                        lines.append(f"- {when}：**{r.get('name','')}**（金额 {amt}）")
                    _reply("最近 20 条匹配：\n" + "\n".join(lines))
        # 自然语言：多少钱/花了多少/买了几次/最常买
        elif any(k in t for k in ["多少钱", "花了多少", "花多少", "总共", "总计", "金额"]):
            # 尝试提取关键词：去掉一些停用词
            q = re.sub(r"[，,。.!！?？]", " ", t)
            q = re.sub(r"(最近\s*\d+\s*天|最近一周|近一周|本周|这周|上周|本月|这个月|上月)", " ", q)
            q = re.sub(r"(我|帮我|请|一下|统计|看看|多少|花了|花|总共|总计|金额|钱|元|块)", " ", q)
            q = re.sub(r"\s{2,}", " ", q).strip()
            q = q if q and len(q) <= 30 else ""

            stt = stats_items(DB_DSN, user_id=user_id, name_like=q or None, from_date=from_s, to_date=to_s)
            if q:
                _reply(f"{range_label}（包含“{q}”）一共 **{stt['cnt']}** 条记录，累计金额 **{stt['total_amount']}**。")
            else:
                _reply(f"{range_label}一共 **{stt['cnt']}** 条记录，累计金额 **{stt['total_amount']}**。你也可以说：比如“最近7天 拿铁 花了多少”。")
        elif any(k in t for k in ["几次", "次数", "买了几回", "买了几次", "出现几次"]):
            q = re.sub(r"[，,。.!！?？]", " ", t)
            q = re.sub(r"(最近\s*\d+\s*天|最近一周|近一周|本周|这周|上周|本月|这个月|上月)", " ", q)
            q = re.sub(r"(我|帮我|请|一下|统计|看看|几次|次数|买了|出现)", " ", q)
            q = re.sub(r"\s{2,}", " ", q).strip()
            q = q if q and len(q) <= 30 else ""

            stt = stats_items(DB_DSN, user_id=user_id, name_like=q or None, from_date=from_s, to_date=to_s)
            if q:
                _reply(f"{range_label}（包含“{q}”）出现 **{stt['cnt']}** 次。")
            else:
                _reply(f"{range_label}总记录数 **{stt['cnt']}**。你也可以说：比如“上周 拿铁 买了几次”。")
        elif any(k in t for k in ["最常买", "最常点", "top", "排行", "排名", "最多"]):
            mm = re.search(r"(\d+)", t)
            n = int(mm.group(1)) if mm else 10
            n = max(1, min(n, 50))
            rows = (
                top_items_filtered(DB_DSN, user_id=user_id, from_date=from_s, to_date=to_s, limit=n)
                if (from_s or to_s)
                else top_items(DB_DSN, user_id=user_id, limit=n)
            )
            if not rows:
                _reply("数据库里还没有行项目数据。先入库几张小票再试。")
            else:
                lines = [f"- **{r['name']}**：{r['cnt']} 次，累计金额 {r['total_amount']}" for r in rows]
                _reply(f"{range_label}最常买 Top {n}：\n" + "\n".join(lines))
        elif any(k in t for k in ["买了哪些", "有哪些菜", "都买了什么", "都点了什么", "买了啥", "点了啥", "吃了什么"]):
            n = 30
            rows = (
                top_items_filtered(DB_DSN, user_id=user_id, from_date=from_s, to_date=to_s, limit=n)
                if (from_s or to_s)
                else top_items(DB_DSN, user_id=user_id, limit=n)
            )
            if not rows:
                _reply("我这边还没查到记录。先入库几张小票再试。")
            else:
                names = "、".join([r["name"] for r in rows])
                _reply(f"{range_label}（最多列 {n} 个）：{names}")
        elif low.startswith("搜") or low.startswith("search"):
            if not allow_web:
                _reply("你还没开启“允许联网搜索”。先在侧边栏勾选它，再输入：`搜 关键词`。")
            else:
                q = t.split(" ", 1)[1].strip() if " " in t else t.replace("搜", "", 1).strip()
                if not q:
                    _reply("你要搜什么？例如：`搜 NUT CO 缩写`。")
                else:
                    try:
                        with st.spinner("联网搜索中..."):
                            rs = ddg_search(q, max_results=5)
                        if not rs:
                            _reply("没搜到结果（或被拦截）。你可以换个关键词再试。")
                        else:
                            lines = []
                            for i, r in enumerate(rs, start=1):
                                s = f"{i}. **{r.title}**\n   - {r.url}"
                                if r.snippet:
                                    s += f"\n   - {r.snippet}"
                                lines.append(s)
                            _reply("我给你找到了这些结果：\n" + "\n".join(lines))
                    except Exception as e:
                        _reply(f"联网搜索失败：{e}")
        else:
            if allow_web:
                # 联网开启时：没匹配到本地意图就直接帮你搜（不用加“搜”前缀）
                try:
                    with st.spinner("联网搜索中..."):
                        rs = ddg_search(t, max_results=5)
                    if not rs:
                        _reply("我没匹配到本地意图，也没搜到结果。你可以换个问法或关键词再试。")
                    else:
                        lines = []
                        for i, r in enumerate(rs, start=1):
                            s = f"{i}. **{r.title}**\n   - {r.url}"
                            if r.snippet:
                                s += f"\n   - {r.snippet}"
                            lines.append(s)
                        _reply("我没匹配到本地指令，但我帮你联网搜到了这些：\n" + "\n".join(lines))
                except Exception as e:
                    _reply(f"我没匹配到本地意图，且联网搜索失败：{e}")
            else:
                _reply("我没太听懂。你可以直接用中文问：例如“最近7天买了哪些菜”“上月拿铁花了多少”“本周拿铁买了几次”，或用命令：`top 20`/`查 关键词`/`教 A -> B`/`建议 A`/`看别名`。")

        st.rerun()

tab_scan, tab_query = st.tabs(["扫描入库", "查询导出"])

with tab_scan:
    st.subheader("上传小票并识别")
    uploaded = st.file_uploader("选择小票文件（图片或 PDF）", type=["png", "jpg", "jpeg", "webp", "pdf"])
    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        ocr_lang_label = st.selectbox("OCR 语言", options=["中文", "英文"], index=0)
        lang = "chi_sim+eng" if ocr_lang_label == "中文" else "eng"
    with col_b:
        max_pages = st.number_input("PDF 最多处理页数", min_value=1, max_value=20, value=6)
    with col_c:
        use_local_learn = st.checkbox("使用本地学习（自动还原缩写/去噪，推荐）", value=True)

    if uploaded is not None:
        source_name = uploaded.name
        file_bytes = uploaded.getvalue()

        is_pdf = source_name.lower().endswith(".pdf")
        if not is_pdf:
            st.image(file_bytes, caption=source_name, use_container_width=True)

        if st.button("开始识别", type="primary"):
            with st.spinner("识别中..."):
                if is_pdf:
                    ocr = ocr_pdf_bytes(file_bytes, lang=lang, max_pages=int(max_pages))
                else:
                    ocr = ocr_image_bytes(file_bytes, lang=lang)

            st.success(f"识别完成（引擎：{ocr.engine}）")
            if ocr.notes:
                st.info(ocr.notes)

            st.session_state["last_ocr_text"] = ocr.text
            st.session_state["last_ocr_meta"] = {
                "source_name": source_name,
                "ocr_engine": ocr.engine,
                "ocr_notes": ocr.notes,
                "use_local_learn": bool(use_local_learn),
            }

    st.divider()
    st.subheader("校对并入库")

    ocr_text = st.session_state.get("last_ocr_text", "")
    if ocr_text:
        with st.expander("查看 OCR 原文（可复制）", expanded=False):
            st.text_area("OCR 文本", value=ocr_text, height=240)

        meta = st.session_state.get("last_ocr_meta", {})
        items = parse_receipt_text(ocr_text)
        df = pd.DataFrame(items_to_rows(items))

        # 本地学习：把已学到的“缩写/错字 → 标准菜名”建议出来，并对 100% 命中自动替换
        if meta.get("use_local_learn") and not df.empty and "name" in df.columns:
            suggested = []
            for n in df["name"].tolist():
                s = suggest_canonical(DB_DSN, user_id=user_id, raw_name=str(n))
                suggested.append("" if not s else f"{s.canonical}（{s.score}%）")
            df.insert(len(df.columns), "suggested", suggested)
            for i, s in enumerate(suggested):
                if s and s.endswith("（100%）"):
                    df.at[i, "name"] = s.split("（", 1)[0]

        if df.empty:
            st.warning("自动提取没提到行项目，我给你打开“兜底手动选行”模式：从 OCR 每一行里勾选菜名并保存。")
            fdf = _fallback_rows_from_ocr(ocr_text)
            if fdf.empty:
                st.error("OCR 原文几乎为空/无有效行。建议换更清晰的照片，或切换 OCR 语言后再试。")
            else:
                st.caption("把非菜品行的 include 取消勾选，把 name 改成你想记录的菜名，然后保存到数据库。")
                edited = st.data_editor(
                    fdf,
                    num_rows="dynamic",
                    use_container_width=True,
                    column_config={
                        "include": st.column_config.CheckboxColumn("入库", help="只入库勾选的行"),
                        "name": st.column_config.TextColumn("名称", required=True),
                        "qty": st.column_config.NumberColumn("数量"),
                        "unit_price": st.column_config.NumberColumn("单价"),
                        "amount": st.column_config.NumberColumn("金额"),
                        "raw": st.column_config.TextColumn("原始行", disabled=True),
                    },
                    key="editor_items_fallback",
                )

                col1, col2, col3 = st.columns([1, 1, 2])
                with col1:
                    do_save = st.button("保存到数据库", type="primary", key="save_fallback")
                with col2:
                    st.write("")
                with col3:
                    st.caption(f"将保存到：`{DB_DSN}`")

                if do_save:
                    # 学习：你把缩写改成标准名，也会被记住
                    try:
                        for idx in range(min(len(fdf), len(edited))):
                            b = str(fdf.iloc[idx].get("name", "")).strip()
                            a = str(edited.iloc[idx].get("name", "")).strip()
                            learn_from_edit(DB_DSN, user_id=user_id, before_name=b, after_name=a)
                    except Exception:
                        pass

                    rows = edited.to_dict(orient="records")
                    rows = [r for r in rows if r.get("include") and (r.get("name") or "").strip()]
                    for r in rows:
                        r.pop("include", None)
                    receipt_id = insert_receipt(
                        DB_DSN,
                        user_id=user_id,
                        source_name=meta.get("source_name"),
                        ocr_engine=meta.get("ocr_engine"),
                        ocr_notes=meta.get("ocr_notes"),
                        ocr_text=ocr_text,
                    )
                    insert_items(DB_DSN, user_id=user_id, receipt_id=receipt_id, rows=rows)
                    st.success(f"已入库：receipt_id={receipt_id}，行项目 {len(rows)} 条。")
        else:
            st.caption("下面是自动提取结果。请在表格中校对后再保存。")
            edited = st.data_editor(
                df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "name": st.column_config.TextColumn("名称", required=True),
                    "qty": st.column_config.NumberColumn("数量"),
                    "unit_price": st.column_config.NumberColumn("单价"),
                    "amount": st.column_config.NumberColumn("金额"),
                    "raw": st.column_config.TextColumn("原始行", disabled=True),
                    "suggested": st.column_config.TextColumn("本地学习建议", disabled=True),
                },
                key="editor_items",
            )

            col1, col2, col3 = st.columns([1, 1, 2])
            with col1:
                do_save = st.button("保存到数据库", type="primary")
            with col2:
                st.write("")
            with col3:
                st.caption(f"将保存到：`{DB_DSN}`")

            if do_save:
                # 学习：把你这次手工改名的映射记下来，下次自动还原
                try:
                    for idx in range(min(len(df), len(edited))):
                        b = str(df.iloc[idx].get("name", "")).strip()
                        a = str(edited.iloc[idx].get("name", "")).strip()
                        learn_from_edit(DB_DSN, user_id=user_id, before_name=b, after_name=a)
                except Exception:
                    pass

                rows = edited.to_dict(orient="records")
                rows = [r for r in rows if (r.get("name") or "").strip()]
                receipt_id = insert_receipt(
                    DB_DSN,
                    user_id=user_id,
                    source_name=meta.get("source_name"),
                    ocr_engine=meta.get("ocr_engine"),
                    ocr_notes=meta.get("ocr_notes"),
                    ocr_text=ocr_text,
                )
                insert_items(DB_DSN, user_id=user_id, receipt_id=receipt_id, rows=rows)
                st.success(f"已入库：receipt_id={receipt_id}，行项目 {len(rows)} 条。")
    else:
        st.info("先上传小票并点击“开始识别”。")

    st.divider()
    st.subheader("最近入库的小票")
    recs = list_receipts(DB_DSN, user_id=user_id, limit=10)
    if recs:
        st.dataframe(
            pd.DataFrame([r.__dict__ for r in recs]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("暂无数据。")

with tab_query:
    st.subheader("按名称/时间查询行项目")
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        name_like = st.text_input("名称包含", value="")
    with col2:
        from_d = st.date_input("起始日期", value=None)
    with col3:
        to_d = st.date_input("结束日期", value=None)
    with col4:
        limit = st.number_input("最多条数", min_value=50, max_value=5000, value=500, step=50)

    from_s, to_s = _dt_range(from_d, to_d)
    rows = query_items(DB_DSN, user_id=user_id, name_like=name_like or None, from_date=from_s, to_date=to_s, limit=int(limit))
    dfq = pd.DataFrame(rows)
    st.dataframe(dfq, use_container_width=True, hide_index=True)

    if not dfq.empty:
        csv = dfq.to_csv(index=False).encode("utf-8-sig")
        st.download_button("导出 CSV", data=csv, file_name="items_export.csv", mime="text/csv")

