"""Claude Desktop 딥링크 버튼 — 스킬 3종 트리거용, 개인·가족 대시보드 공용.

Claude Desktop 앱이 설치되어 있어야 동작한다 (무료).
"""

import urllib.parse

import streamlit as st


def claude_url(prompt: str) -> str:
    return f"claude://claude.ai/new?q={urllib.parse.quote(prompt)}"


def render_skill_buttons(name: str, ticker: str, key_prefix: str = "") -> None:
    """종목명 옆에 기업해독/스토리/가격판독 버튼 3개를 나란히 그린다."""
    col_a, col_b, col_c = st.columns(3)
    col_a.link_button("🏢 기업 해독", claude_url(f"{name} 분석해줘"),
                      use_container_width=True, key=f"{key_prefix}dec_{ticker}")
    col_b.link_button("📖 스토리", claude_url(f"{name} 스토리 분석해줘"),
                      use_container_width=True, key=f"{key_prefix}story_{ticker}")
    col_c.link_button("💰 가격 판독", claude_url(f"{name} 지금 사도 되나?"),
                      use_container_width=True, key=f"{key_prefix}price_{ticker}")


def render_pick_row(name: str, ticker: str, score: float, key_prefix: str = "") -> None:
    """전략 신호 한 줄 (종목명 + 점수 + 버튼 3개)."""
    col_nm, col_score, col_a, col_b, col_c = st.columns([3, 1.2, 1.5, 1.5, 1.5])
    col_nm.write(f"**{name}** `{ticker}`")
    col_score.write(f"{score:+.1%}")
    col_a.link_button("🏢 기업 해독", claude_url(f"{name} 분석해줘"),
                      use_container_width=True, key=f"{key_prefix}dec_{ticker}")
    col_b.link_button("📖 스토리", claude_url(f"{name} 스토리 분석해줘"),
                      use_container_width=True, key=f"{key_prefix}story_{ticker}")
    col_c.link_button("💰 가격 판독", claude_url(f"{name} 지금 사도 되나?"),
                      use_container_width=True, key=f"{key_prefix}price_{ticker}")


def section_stock_search(key_prefix: str = "") -> None:
    """아무 종목이나 검색해서 분석 버튼을 띄우는 섹션."""
    st.subheader("종목 검색")
    st.caption("전략이 뽑아준 종목 말고, 궁금한 종목을 직접 찾아볼 수 있습니다. "
              "전체 상장종목 대상입니다.")

    query = st.text_input("종목명 또는 종목코드", key=f"{key_prefix}search_q",
                          placeholder="예: 카카오, LG전자, 005930")
    if not query:
        return

    try:
        import stock_search
        listing = _cached_listing()
        results = stock_search.search(query, listing)
    except Exception as e:
        st.error(f"종목 목록을 불러오지 못했습니다: {e}")
        return

    if results.empty:
        st.write("검색 결과가 없습니다.")
        return

    for _, row in results.iterrows():
        nm, tk, mkt = row["Name"], row["Code"], row["Market"]
        col_nm, col_a, col_b, col_c = st.columns([2.5, 1.5, 1.5, 1.5])
        col_nm.write(f"**{nm}** `{tk}` · {mkt}")
        col_a.link_button("🏢 기업 해독", claude_url(f"{nm} 분석해줘"),
                          use_container_width=True, key=f"{key_prefix}s_dec_{tk}")
        col_b.link_button("📖 스토리", claude_url(f"{nm} 스토리 분석해줘"),
                          use_container_width=True, key=f"{key_prefix}s_story_{tk}")
        col_c.link_button("💰 가격 판독", claude_url(f"{nm} 지금 사도 되나?"),
                          use_container_width=True, key=f"{key_prefix}s_price_{tk}")


@st.cache_data(ttl=3600 * 12)   # 하루 두 번이면 충분 — 종목 목록은 자주 안 바뀜
def _cached_listing():
    import stock_search
    return stock_search.load_listing()
