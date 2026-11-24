"""M1 巨潮公告客户端测试（离线，全部走注入点，不真实联网）。"""

from __future__ import annotations

from typing import Any

import pytest

from zhuang_monitor.sources import cninfo


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """每个用例前清空模块级缓存，避免用例间互相污染。"""
    cninfo._clear_cache()


def test_column_for_sse_szse() -> None:
    assert cninfo.column_for("600519") == "sse"
    assert cninfo.column_for("688981") == "sse"
    assert cninfo.column_for("000001") == "szse"
    assert cninfo.column_for("002348") == "szse"
    assert cninfo.column_for("300750") == "szse"
    assert cninfo.column_for("301000") == "szse"


SAMPLE_ORGID_PAYLOAD = {
    "stockList": [
        {"code": "002348", "orgId": "gssz000002348", "zwjc": "高乐股份"},
        {"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"},
        {"code": "688981", "orgId": "shne0688981", "zwjc": "中芯国际"},
    ]
}


def test_resolve_org_id_hit() -> None:
    got = cninfo.resolve_org_id("002348", fetch_json=lambda url: SAMPLE_ORGID_PAYLOAD)
    assert got == "gssz000002348"
    got = cninfo.resolve_org_id("688981", fetch_json=lambda url: SAMPLE_ORGID_PAYLOAD)
    assert got == "shne0688981"


def test_resolve_org_id_miss_returns_none() -> None:
    got = cninfo.resolve_org_id("999999", fetch_json=lambda url: SAMPLE_ORGID_PAYLOAD)
    assert got is None


def test_resolve_org_id_second_call_no_network() -> None:
    cninfo.resolve_org_id("002348", fetch_json=lambda url: SAMPLE_ORGID_PAYLOAD)

    def boom(url: str) -> dict:
        raise AssertionError("缓存命中后不得再次请求网络")

    assert cninfo.resolve_org_id("002348", fetch_json=boom) == "gssz000002348"
    assert cninfo.resolve_org_id("999999", fetch_json=boom) is None


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """记录每次 post 的 url/data，按顺序吐出预置分页载荷。"""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = list(pages)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, data: dict | None = None, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, dict(data or {})))
        assert self.pages, "请求次数超出预置页数"
        return FakeResponse(self.pages.pop(0))


def _ann(title: str, ms: int, adjunct: str, sec_code: str = "002348") -> dict:
    return {
        "announcementTime": ms,
        "announcementTitle": title,
        "adjunctUrl": adjunct,
        "secCode": sec_code,
    }


def _prime_cache() -> None:
    cninfo.resolve_org_id("002348", fetch_json=lambda url: SAMPLE_ORGID_PAYLOAD)


def test_fetch_single_page_fields_and_cleaning() -> None:
    _prime_cache()
    page = {
        "announcements": [
            _ann('<span id="xx">关于&lt;增资&lt;的公告</span>', 1710460800000, "finalpage/2024-03-15/121951.PDF"),
        ],
        "totalAnnouncement": 1,
        "totalpages": 1,
        "hasMore": False,
    }
    sess = FakeSession([page])
    rows = cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=lambda s: None)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == {"code", "pub_date", "title", "pdf_url", "category", "source"}
    assert row["code"] == "002348"
    assert row["pub_date"] == "2024-03-15"
    assert row["title"] == "关于<增资<的公告"
    assert row["pdf_url"] == "https://static.cninfo.com.cn/finalpage/2024-03-15/121951.PDF"
    assert row["category"] is None
    assert row["source"] == "cninfo"


def test_fetch_pagination_merges_pages() -> None:
    _prime_cache()
    p1 = {"announcements": [_ann("公告一", 1710460800000, "a/1.PDF")], "hasMore": True}
    p2 = {"announcements": [_ann("公告二", 1710547200000, "b/2.PDF")], "hasMore": False}
    sess = FakeSession([p1, p2])
    rows = cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=lambda s: None)
    assert [r["title"] for r in rows] == ["公告一", "公告二"]
    assert [r["pub_date"] for r in rows] == ["2024-03-15", "2024-03-16"]
    assert [d["pageNum"] for _, d in sess.posts] == ["1", "2"] or [d["pageNum"] for _, d in sess.posts] == [1, 2]


def test_fetch_max_pages_stops_early() -> None:
    _prime_cache()
    p1 = {"announcements": [_ann("公告一", 1710460800000, "a/1.PDF")], "hasMore": True}
    sess = FakeSession([p1])
    rows = cninfo.fetch_announcements(
        "002348", "2024-01-01", "2024-03-31", max_pages=1, session=sess, sleeper=lambda s: None
    )
    assert len(rows) == 1
    assert len(sess.posts) == 1


def test_fetch_empty_returns_empty_list() -> None:
    _prime_cache()
    sess = FakeSession([{"announcements": [], "hasMore": False}])
    rows = cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=lambda s: None)
    assert rows == []


def test_fetch_query_params_and_page_size_cap() -> None:
    _prime_cache()
    sess = FakeSession([{"announcements": [], "hasMore": False}])
    cninfo.fetch_announcements(
        "002348", "2024-01-01", "2024-03-31", category="category_ndbg_szsh;category_zeng",
        session=sess, sleeper=lambda s: None,
    )
    url, data = sess.posts[0]
    assert url == "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    assert int(data["pageSize"]) <= 30
    assert str(data["pageSize"]) == "30"
    assert str(data["column"]) == "szse"
    assert data["tabName"] == "fulltext"
    assert data["stock"] == "002348,gssz000002348"
    assert data["seDate"] == "2024-01-01~2024-03-31"
    assert data["category"] == "category_ndbg_szsh;category_zeng"
    assert data["sortField"] == "announcementTime"
    assert data["sortAsc"] == "false"


def test_fetch_rate_limit_sleeps_at_least_1_5s() -> None:
    _prime_cache()
    slept: list[float] = []
    p1 = {"announcements": [_ann("公告一", 1710460800000, "a/1.PDF")], "hasMore": True}
    p2 = {"announcements": [_ann("公告二", 1710547200000, "b/2.PDF")], "hasMore": False}
    sess = FakeSession([p1, p2])
    cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=slept.append)
    assert len(slept) == 2
    assert all(s >= 1.5 for s in slept)
    assert all(s <= 2.0 for s in slept)


def test_fetch_no_adjunct_url_pdf_none() -> None:
    _prime_cache()
    page = {
        "announcements": [{"announcementTime": 1710460800000, "announcementTitle": "无附件公告"}],
        "hasMore": False,
    }
    sess = FakeSession([page])
    rows = cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=lambda s: None)
    assert rows[0]["pdf_url"] is None


def test_title_cleaning_anchor_and_amp_entity() -> None:
    _prime_cache()
    page = {
        "announcements": [
            _ann('<a href="x">关于A&amp;B&lt;重大&lt;合同</a>  <span>（更新）</span>', 1710460800000, "u/1.PDF"),
        ],
        "hasMore": False,
    }
    sess = FakeSession([page])
    rows = cninfo.fetch_announcements("002348", "2024-01-01", "2024-03-31", session=sess, sleeper=lambda s: None)
    assert rows[0]["title"] == "关于A&B<重大<合同 （更新）"
