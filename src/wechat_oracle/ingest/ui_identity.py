"""Best-effort sender identity for the visible WeChat UI backend."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable

from loguru import logger


UI_MEMBER_PREFIX = "ui-member:"
_OCR_LOCK = threading.Lock()
_OCR_ENGINE = None


def normalize_display_name(value: object) -> str:
    text = "".join(
        char for char in str(value or "")
        if unicodedata.category(char) not in {"Cc", "Cf"}
    ).replace("\u2005", " ").strip()
    return re.sub(r"\s+", " ", text)


def stable_ui_member_id(group_id: str, display_name: str) -> str:
    normalized = normalize_display_name(display_name).casefold()
    digest = hashlib.sha256(f"{group_id}\0{normalized}".encode("utf-8")).hexdigest()[:20]
    return f"{UI_MEMBER_PREFIX}{digest}"


def ensure_ui_member_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ui_group_members (
            group_id       TEXT NOT NULL,
            display_name   TEXT NOT NULL,
            synthetic_id   TEXT NOT NULL,
            first_seen_at  REAL NOT NULL,
            last_seen_at   REAL NOT NULL,
            active         INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            source         TEXT NOT NULL DEFAULT 'wx4py-roster',
            PRIMARY KEY(group_id, display_name),
            UNIQUE(group_id, synthetic_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ui_group_members_active
            ON ui_group_members(group_id, active, display_name);
        """
    )


def save_ui_roster(
    conn: sqlite3.Connection,
    group_id: str,
    members: Iterable[object],
) -> list[str]:
    names = sorted({normalize_display_name(item) for item in members if normalize_display_name(item)})
    now = time.time()
    conn.execute("UPDATE ui_group_members SET active=0 WHERE group_id=?", (group_id,))
    for name in names:
        synthetic_id = stable_ui_member_id(group_id, name)
        conn.execute(
            """
            INSERT INTO ui_group_members(
                group_id,display_name,synthetic_id,first_seen_at,last_seen_at,active,source
            ) VALUES(?,?,?,?,?,1,'wx4py-roster')
            ON CONFLICT(group_id,display_name) DO UPDATE SET
                synthetic_id=excluded.synthetic_id,
                last_seen_at=excluded.last_seen_at,
                active=1
            """,
            (group_id, name, synthetic_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO member_profiles(
                group_id,sender_wxid,display_name,profile_json,summary_text,
                locked_sections_json,version,created_at,updated_at
            ) VALUES(?,?,?,?,?,'[]',1,?,?)
            ON CONFLICT(group_id,sender_wxid) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (
                group_id,
                synthetic_id,
                name,
                json.dumps(
                    {
                        "identity": None,
                        "interests": None,
                        "skills": None,
                        "communication_style": None,
                        "habits": None,
                        "relationships": None,
                        "opinions": None,
                        "sensitive_inferences": None,
                        "recent_focus": None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "",
                now,
                now,
            ),
        )
    return names


def load_ui_roster(conn: sqlite3.Connection, group_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT display_name FROM ui_group_members WHERE group_id=? AND active=1 "
        "ORDER BY display_name COLLATE NOCASE",
        (group_id,),
    ).fetchall()
    return [str(row["display_name"]) for row in rows]


def match_roster_name(
    ocr_texts: Iterable[object],
    roster: Iterable[object],
    *,
    minimum_score: float = 0.82,
) -> tuple[str | None, float]:
    texts = [normalize_display_name(item) for item in ocr_texts]
    names = [normalize_display_name(item) for item in roster]
    texts = [item for item in texts if item]
    names = [item for item in names if item]
    scores: list[tuple[float, str]] = []
    for name in names:
        folded_name = name.casefold()
        best = 0.0
        for text in texts:
            folded_text = text.casefold()
            if folded_name == folded_text:
                best = 1.0
                break
            if len(folded_name) >= 2 and folded_name in folded_text:
                best = max(best, 0.97)
            else:
                best = max(best, SequenceMatcher(None, folded_name, folded_text).ratio())
        scores.append((best, name))
    scores.sort(key=lambda item: (-item[0], item[1].casefold()))
    if not scores or scores[0][0] < minimum_score:
        return None, scores[0][0] if scores else 0.0
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.08:
        return None, scores[0][0]
    return scores[0][1], scores[0][0]


def sender_label_texts(ocr_result: Iterable[object]) -> list[str]:
    rows: list[tuple[float, str]] = []
    for item in ocr_result or []:
        try:
            box, text, confidence = item[0], item[1], float(item[2])
            top = min(float(point[1]) for point in box)
        except (IndexError, TypeError, ValueError):
            continue
        normalized = normalize_display_name(text)
        if confidence >= 0.55 and normalized:
            rows.append((top, normalized))
    if len(rows) < 2:
        return []
    rows.sort(key=lambda item: item[0])
    first_top = rows[0][0]
    return [text for top, text in rows if top <= first_top + 12]


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def recognize_control_sender(control, roster: Iterable[str]) -> tuple[str | None, float]:
    names = list(roster)
    if not control or not names:
        return None, 0.0
    try:
        rect = control.BoundingRectangle
        left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        if right <= left or bottom <= top:
            return None, 0.0
        from PIL import ImageGrab
        import numpy as np

        image = ImageGrab.grab(
            bbox=(max(0, left), max(0, top), right, bottom),
            all_screens=True,
        )
        with _OCR_LOCK:
            result, _elapsed = _ocr_engine()(np.asarray(image))
        texts = sender_label_texts(result or [])
        return match_roster_name(texts, names)
    except Exception as exc:
        logger.debug("ui sender OCR failed: {}", exc)
        return None, 0.0
