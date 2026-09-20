import os
import json
import time
from datetime import datetime, timedelta, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.environ["FOOTBALL_DATA_TOKEN"]

BASE_URL = "https://api.football-data.org/v4"

HEADERS = {
    "X-Auth-Token": TOKEN
}

SEASON = 2026

DATA_DIR = "data"
LIVE_FILE = os.path.join(DATA_DIR, "live.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# football-data.org competition codes
LEAGUES = {
    "PL": "England Premier League",
    "ELC": "England Championship",
    "FL1": "France Ligue 1",
    "BL1": "Germany Bundesliga",
    "PPL": "Portugal Liga Portugal",
    "DSU": "Denmark Superliga",
    "SA": "Italy Serie A",
    "PD": "Spain LaLiga",
    "ABL": "Austria Bundesliga",
}


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):
    url = BASE_URL + endpoint

    response = requests.get(
        url,
        headers=HEADERS,
        params=params or {},
        timeout=30
    )

    if response.status_code == 403:
        raise RuntimeError(
            "football-data.org returned 403 Forbidden. "
            "This competition may not be available on the current API plan."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "football-data.org rate limit reached."
        )

    response.raise_for_status()

    return response.json()


# ============================================================
# MATCH HELPERS
# ============================================================

def fixture_date(match):
    return match.get("utcDate", "")


def teams(match):
    home = match.get("homeTeam", {})
    away = match.get("awayTeam", {})

    return (
        home.get("id"),
        away.get("id"),
        home.get("name", "Unknown"),
        away.get("name", "Unknown")
    )


def score(match):
    full_time = match.get("score", {}).get("fullTime", {})

    return (
        full_time.get("home"),
        full_time.get("away")
    )


def is_finished(match):
    return match.get("status") in {
        "FINISHED",
        "AWARDED"
    }


def result_type(match):
    home_goals, away_goals = score(match)

    if home_goals is None or away_goals is None:
        return None

    if home_goals == away_goals:
        if home_goals == 0:
            return "draw"
        return "draw_goals"

    if home_goals > away_goals:
        return "a_win"

    return "b_win"


def sort_newest(matches):
    return sorted(
        matches,
        key=lambda x: fixture_date(x),
        reverse=True
    )


# ============================================================
# PATTERN 1
# ============================================================

PATTERN_1_A = [
    "a_win",
    "draw_goals"
]

PATTERN_1_B = [
    "draw_goals",
    "b_win"
]


def pattern1_h2h(matches):
    finished = [
        m for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(finished)

    results = []

    for match in finished[:10]:
        result = result_type(match)

        if result:
            results.append(result)

    if results[:2] == PATTERN_1_A:
        return True, "home"

    if results[:2] == PATTERN_1_B:
        return True, "away"

    return False, None


# ============================================================
# PATTERN 2
# ============================================================

PATTERN_2 = [
    "draw_goals",
    "draw_goals",
    "a_win",
    "b_win"
]


def pattern2_h2h(matches):
    finished = [
        m for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(finished)

    results = []

    for match in finished[:10]:
        result = result_type(match)

        if result:
            results.append(result)

    return results[:4] == PATTERN_2


# ============================================================
# PATTERN 3
# ============================================================

PATTERN_3 = [
    "a_win",
    "a_win",
    "draw",
    "b_win",
    "b_win"
]


def pattern3_h2h(matches):
    finished = [
        m for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(finished)

    results = []

    for match in finished[:10]:
        result = result_type(match)

        if result:
            results.append(result)

    return results[:5] == PATTERN_3


# ============================================================
# TEAM-SPECIFIC PATTERN 1
# ============================================================

def team_result(match, team_id):
    home_id, away_id, _, _ = teams(match)
    home_goals, away_goals = score(match)

    if home_goals is None or away_goals is None:
        return None

    if team_id == home_id:
        team_goals = home_goals
        opponent_goals = away_goals
    elif team_id == away_id:
        team_goals = away_goals
        opponent_goals = home_goals
    else:
        return None

    if team_goals > opponent_goals:
        return "win"

    if team_goals == opponent_goals:
        if team_goals == 0:
            return "draw"
        return "draw_goals"

    return "loss"


def team_pattern1(
    matches,
    team_id,
    upcoming_opponent_id,
    before_date
):
    cutoff = datetime.fromisoformat(
        before_date.replace("Z", "+00:00")
    )

    recent = []

    for match in matches:

        if not is_finished(match):
            continue

        match_date = fixture_date(match)

        if not match_date:
            continue

        match_dt = datetime.fromisoformat(
            match_date.replace("Z", "+00:00")
        )

        if match_dt >= cutoff:
            continue

        home_id, away_id, _, _ = teams(match)

        if team_id not in {home_id, away_id}:
            continue

        opponent_id = (
            away_id
            if team_id == home_id
            else home_id
        )

        # Same condition as the original scanner:
        # exclude matches against the upcoming opponent.
        if opponent_id == upcoming_opponent_id:
            continue

        recent.append(match)

    recent = sort_newest(recent)[:2]

    results = []

    for match in recent:
        result = team_result(match, team_id)

        if result:
            results.append(result)

    if results == ["win", "draw_goals"]:
        return True

    if results == ["draw_goals", "win"]:
        return True

    return False


# ============================================================
# H2H
# ============================================================

def get_h2h(
    all_matches,
    home_id,
    away_id,
    before_date
):
    cutoff = datetime.fromisoformat(
        before_date.replace("Z", "+00:00")
    )

    h2h = []

    for match in all_matches:

        if not is_finished(match):
            continue

        match_date = fixture_date(match)

        if not match_date:
            continue

        match_dt = datetime.fromisoformat(
            match_date.replace("Z", "+00:00")
        )

        if match_dt >= cutoff:
            continue

        match_home_id, match_away_id, _, _ = teams(match)

        if {
            match_home_id,
            match_away_id
        } == {
            home_id,
            away_id
        }:
            h2h.append(match)

    return sort_newest(h2h)[:10]


# ============================================================
# RECENT TEAM MATCHES
# ============================================================

def get_recent_team_games(
    all_matches,
    team_id,
    upcoming_opponent_id,
    before_date
):
    cutoff = datetime.fromisoformat(
        before_date.replace("Z", "+00:00")
    )

    sixty_days_ago = cutoff - timedelta(days=60)

    recent = []

    for match in all_matches:

        if not is_finished(match):
            continue

        match_date = fixture_date(match)

        if not match_date:
            continue

        match_dt = datetime.fromisoformat(
            match_date.replace("Z", "+00:00")
        )

        if match_dt >= cutoff:
            continue

        if match_dt < sixty_days_ago:
            continue

        home_id, away_id, _, _ = teams(match)

        if team_id not in {home_id, away_id}:
            continue

        opponent_id = (
            away_id
            if team_id == home_id
            else home_id
        )

        if opponent_id == upcoming_opponent_id:
            continue

        recent.append(match)

    return sort_newest(recent)


# ============================================================
# EVALUATE MATCH
# ============================================================

def evaluate_match(
    match,
    league_code,
    league_name,
    all_matches
):
    (
        home_id,
        away_id,
        home_name,
        away_name
    ) = teams(match)

    match_date = fixture_date(match)

    if not home_id or not away_id:
        return None

    h2h = get_h2h(
        all_matches,
        home_id,
        away_id,
        match_date
    )

    if len(h2h) < 2:
        return None

    h2h_sequence = [
        result_type(m)
        for m in h2h
        if result_type(m)
    ]

    # --------------------------------------------------------
    # PATTERN 1
    # --------------------------------------------------------

    pattern1_match, target = pattern1_h2h(h2h)

    if pattern1_match:

        target_team = (
            home_name
            if target == "home"
            else away_name
        )

        result = {
            "league": league_name,
            "league_code": league_code,
            "date": match_date,
            "home": home_name,
            "away": away_name,
            "market": "Over 0.5 team goals",
            "target_team": target_team,
            "pattern": "Pattern 1",
            "h2h_sequence": h2h_sequence
        }

        # ----------------------------------------------------
        # PATTERN 4
        # ----------------------------------------------------

        home_recent = get_recent_team_games(
            all_matches,
            home_id,
            away_id,
            match_date
        )

        away_recent = get_recent_team_games(
            all_matches,
            away_id,
            home_id,
            match_date
        )

        home_pattern4 = team_pattern1(
            home_recent,
            home_id,
            away_id,
            match_date
        )

        away_pattern4 = team_pattern1(
            away_recent,
            away_id,
            home_id,
            match_date
        )

        if home_pattern4 and away_pattern4:
            result["pattern"] = "Pattern 4"
            result["market"] = "Pattern 4"

        return result

    # --------------------------------------------------------
    # PATTERN 2
    # --------------------------------------------------------

    if pattern2_h2h(h2h):
        return {
            "league": league_name,
            "league_code": league_code,
            "date": match_date,
            "home": home_name,
            "away": away_name,
            "market": "Over 1.5 goals",
            "pattern": "Pattern 2",
            "h2h_sequence": h2h_sequence
        }

    # --------------------------------------------------------
    # PATTERN 3
    # --------------------------------------------------------

    if pattern3_h2h(h2h):
        return {
            "league": league_name,
            "league_code": league_code,
            "date": match_date,
            "home": home_name,
            "away": away_name,
            "market": "Over 1.5 goals",
            "pattern": "Pattern 3",
            "h2h_sequence": h2h_sequence
        }

    return None


# ============================================================
# FILE HELPERS
# ============================================================

def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# MAIN
# ============================================================

def main():

    now = datetime.now(timezone.utc)

    today = now.date()

    end_date = today + timedelta(days=7)

    date_from = today - timedelta(days=365)

    date_to = end_date

    all_results = []

    league_status = []

    print("========================================")
    print("FOOTBALL PATTERN SCANNER")
    print("========================================")
    print("Season:", SEASON)
    print("Scanning:", today, "through", end_date)
    print("========================================")

    for league_code, league_name in LEAGUES.items():

        print()
        print("----------------------------------------")
        print("League:", league_name)
        print("Code:", league_code)
        print("----------------------------------------")

        try:

            data = api_get(
                f"/competitions/{league_code}/matches",
                {
                    "season": SEASON,
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat()
                }
            )

            matches = data.get("matches", [])

            print("Matches returned:", len(matches))

            league_status.append({
                "league": league_name,
                "code": league_code,
                "status": "OK",
                "matches": len(matches)
            })

            # Upcoming matches only
            upcoming = []

            for match in matches:

                status = match.get("status")

                if status not in {
                    "SCHEDULED",
                    "TIMED"
                }:
                    continue

                match_date = fixture_date(match)

                if not match_date:
                    continue

                match_dt = datetime.fromisoformat(
                    match_date.replace("Z", "+00:00")
                )

                if match_dt < now:
                    continue

                if match_dt.date() > end_date:
                    continue

                upcoming.append(match)

            upcoming = sorted(
                upcoming,
                key=lambda x: fixture_date(x)
            )

            print("Upcoming matches:", len(upcoming))

            for match in upcoming:

                home_id, away_id, home_name, away_name = teams(match)

                print(
                    "Checking:",
                    home_name,
                    "vs",
                    away_name
                )

                try:

                    result = evaluate_match(
                        match,
                        league_code,
                        league_name,
                        matches
                    )

                    if result:

                        print(
                            "  MATCHED:",
                            result["pattern"],
                            "|",
                            result["market"]
                        )

                        all_results.append(result)

                    else:
                        print("  No pattern")

                except Exception as match_error:

                    print(
                        "  Match error:",
                        str(match_error)
                    )

                # Small pause between processing
                time.sleep(0.2)

        except Exception as league_error:

            print(
                "SKIPPED:",
                league_name,
                "|",
                str(league_error)
            )

            league_status.append({
                "league": league_name,
                "code": league_code,
                "status": "ERROR",
                "error": str(league_error)
            })

        # Pause between league requests
        time.sleep(1)

    # ========================================================
    # OUTPUT
    # ========================================================

    all_results = sorted(
        all_results,
        key=lambda x: x.get("date", "")
    )

    output = {
        "updated_at": now.isoformat(),
        "season": SEASON,
        "date_from": str(today),
        "date_to": str(end_date),
        "matches": all_results,
        "league_status": league_status
    }

    save_json(
        LIVE_FILE,
        output
    )

    # ========================================================
    # HISTORY
    # ========================================================

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(history, list):
        history = []

    history.append(output)

    cutoff_history = now - timedelta(days=14)

    cleaned_history = []

    for item in history:

        timestamp = item.get("updated_at")

        if not timestamp:
            continue

        try:
            item_dt = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )

            if item_dt >= cutoff_history:
                cleaned_history.append(item)

        except Exception:
            continue

    save_json(
        HISTORY_FILE,
        cleaned_history
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("========================================")
    print("SCAN COMPLETE")
    print("========================================")
    print("Pattern matches:", len(all_results))

    for result in all_results:

        print(
            result["league"],
            "|",
            result["home"],
            "vs",
            result["away"],
            "|",
            result["pattern"],
            "|",
            result["market"]
        )

    print("========================================")


if __name__ == "__main__":
    main()
