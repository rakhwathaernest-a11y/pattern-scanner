import os
import json
import time
from datetime import datetime, timedelta, timezone

import requests


API_KEY = os.environ["API_FOOTBALL_KEY"]
BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

# League IDs
LEAGUES = {
    39: "England Premier League",
    40: "England Championship",
    61: "France Ligue 1",
    78: "Germany Bundesliga",
    94: "Portugal Liga Portugal",
    119: "Denmark Superliga",
    135: "Italy Serie A",
    140: "Spain LaLiga",
    218: "Austria Bundesliga",
    288: "South Africa Premiership",
}

SEASON = 2026

DATA_DIR = "data"
LIVE_FILE = os.path.join(DATA_DIR, "live.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")


def api_get(endpoint, params):
    """Call API-Football and return response data."""
    url = BASE_URL + endpoint

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "errors" in data and data["errors"]:
        raise RuntimeError(str(data["errors"]))

    return data.get("response", [])


def fixture_date(fixture):
    return fixture["fixture"]["date"]


def teams(fixture):
    return (
        fixture["teams"]["home"]["id"],
        fixture["teams"]["away"]["id"]
    )


def score(fixture):
    goals = fixture.get("goals", {})
    return (
        goals.get("home"),
        goals.get("away")
    )


def result_type(fixture):
    """Return result from the fixed Home/Away perspective."""

    home_goals, away_goals = score(fixture)

    if home_goals is None or away_goals is None:
        return None

    if home_goals == away_goals:
        if home_goals > 0:
            return "draw_goals"
        return "draw"

    if home_goals > away_goals:
        return "a_win"

    return "b_win"


def is_finished(fixture):
    status = fixture["fixture"]["status"]["short"]
    return status in {
        "FT",
        "AET",
        "PEN"
    }


def sort_newest(fixtures):
    return sorted(
        fixtures,
        key=fixture_date,
        reverse=True
    )


def pattern1(results):
    """
    Pattern 1:

    A win followed by draw with goals
    OR
    draw with goals followed by B win

    Returns:
        target = "home" / "away"
        or None
    """

    if len(results) < 2:
        return None

    first = results[0]
    second = results[1]

    if first == "a_win" and second == "draw_goals":
        return "home"

    if first == "draw_goals" and second == "b_win":
        return "away"

    return None


def pattern2(results):
    """
    Pattern 2:
    draw+goals
    draw+goals
    home win
    away win
    """

    wanted = [
        "draw_goals",
        "draw_goals",
        "a_win",
        "b_win"
    ]

    return len(results) >= 4 and results[:4] == wanted


def pattern3(results):
    """
    Pattern 3:
    home win
    home win
    draw
    away win
    away win
    """

    wanted = [
        "a_win",
        "a_win",
        "draw",
        "b_win",
        "b_win"
    ]

    return len(results) >= 5 and results[:5] == wanted


def get_h2h(home_id, away_id, league_id):
    """
    League-filtered H2H.
    This prevents cup/friendly meetings from entering the patterns.
    """

    return api_get(
        "/fixtures/headtohead",
        {
            "h2h": f"{home_id}-{away_id}",
            "league": league_id,
            "season": SEASON,
            "last": 10
        }
    )


def get_recent_team_games(team_id, league_id, before_date):
    """
    Get recent completed league games for a team,
    excluding the upcoming opponent later in the process.
    """

    games = api_get(
        "/fixtures",
        {
            "league": league_id,
            "season": SEASON,
            "team": team_id,
            "from": (
                datetime.fromisoformat(
                    before_date.replace("Z", "+00:00")
                ) - timedelta(days=60)
            ).date().isoformat(),
            "to": (
                datetime.fromisoformat(
                    before_date.replace("Z", "+00:00")
                ) - timedelta(days=1)
            ).date().isoformat(),
            "status": "FT-AET-PEN"
        }
    )

    return sort_newest(
        [g for g in games if is_finished(g)]
    )


def team_pattern1(team_id, opponent_id, games):
    """
    Evaluate the latest two NON-H2H league games
    from the selected team's perspective.
    """

    filtered = []

    for game in games:
        home_id, away_id = teams(game)

        # Exclude games against the upcoming opponent.
        if opponent_id in (home_id, away_id):
            continue

        if team_id not in (home_id, away_id):
            continue

        home_goals, away_goals = score(game)

        if home_goals is None or away_goals is None:
            continue

        if home_id == team_id:
            if home_goals > away_goals:
                r = "win"
            elif home_goals == away_goals and home_goals > 0:
                r = "draw_goals"
            else:
                r = "other"
        else:
            if away_goals > home_goals:
                r = "win"
            elif away_goals == home_goals and away_goals > 0:
                r = "draw_goals"
            else:
                r = "other"

        filtered.append(r)

        if len(filtered) == 2:
            break

    if len(filtered) < 2:
        return False

    return (
        filtered == ["win", "draw_goals"]
        or
        filtered == ["draw_goals", "win"]
    )


def evaluate_match(fixture, league_id, league_name):
    home_id, away_id = teams(fixture)

    home_name = fixture["teams"]["home"]["name"]
    away_name = fixture["teams"]["away"]["name"]

    h2h = get_h2h(
        home_id,
        away_id,
        league_id
    )

    h2h = [
        g for g in h2h
        if is_finished(g)
    ]

    h2h = sort_newest(h2h)

    h2h_results = [
        result_type(g)
        for g in h2h
    ]

    h2h_results = [
        r for r in h2h_results
        if r is not None
    ]

    matches = []

    # -------------------------
    # PATTERN 1
    # -------------------------

    p1_target = pattern1(h2h_results)

    if p1_target:
        target_team = (
            home_name
            if p1_target == "home"
            else away_name
        )

        matches.append({
            "pattern": 1,
            "market": "Over 0.5 team goals",
            "target_team": target_team,
            "reason": h2h_results[:2]
        })

    # -------------------------
    # PATTERN 2
    # -------------------------

    if pattern2(h2h_results):
        matches.append({
            "pattern": 2,
            "market": "Over 1.5 goals",
            "target_team": None,
            "reason": h2h_results[:4]
        })

    # -------------------------
    # PATTERN 3
    # -------------------------

    if pattern3(h2h_results):
        matches.append({
            "pattern": 3,
            "market": "Over 1.5 goals",
            "target_team": None,
            "reason": h2h_results[:5]
        })

    # -------------------------
    # PATTERN 4
    # -------------------------

    if p1_target:

        home_games = get_recent_team_games(
            home_id,
            league_id,
            fixture_date(fixture)
        )

        away_games = get_recent_team_games(
            away_id,
            league_id,
            fixture_date(fixture)
        )

        home_ok = team_pattern1(
            home_id,
            away_id,
            home_games
        )

        away_ok = team_pattern1(
            away_id,
            home_id,
            away_games
        )

        if home_ok and away_ok:
            matches.append({
                "pattern": 4,
                "market": "Pattern 4",
                "target_team": (
                    home_name
                    if p1_target == "home"
                    else away_name
                ),
                "reason": {
                    "h2h": h2h_results[:2],
                    "home_team": home_name,
                    "away_team": away_name
                }
            })

    if not matches:
        return None

    return {
        "fixture_id": fixture["fixture"]["id"],
        "date": fixture_date(fixture),
        "league_id": league_id,
        "league": league_name,
        "home": home_name,
        "away": away_name,
        "matches": matches,
        "h2h_sequence": h2h_results[:10]
    }


def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )


def main():

    os.makedirs(DATA_DIR, exist_ok=True)

    now = datetime.now(timezone.utc)

    today = now.date()

    # Scan today through the next 7 days.
    end_date = today + timedelta(days=7)

    all_qualifiers = []

    scanned_leagues = []

    for league_id, league_name in LEAGUES.items():

        print(
            f"Scanning {league_name}..."
        )

        try:

            fixtures = api_get(
                "/fixtures",
                {
                    "league": league_id,
                    "season": SEASON,
                    "from": today.isoformat(),
                    "to": end_date.isoformat()
                }
            )

            upcoming = []

            for fixture in fixtures:

                status = fixture["fixture"]["status"]["short"]

                if status in {
                    "NS",
                    "TBD",
                    "PST"
                }:
                    upcoming.append(fixture)

            scanned_leagues.append({
                "id": league_id,
                "name": league_name,
                "fixtures_checked": len(upcoming)
            })

            for fixture in upcoming:

                try:

                    result = evaluate_match(
                        fixture,
                        league_id,
                        league_name
                    )

                    if result:
                        all_qualifiers.append(result)

                except Exception as e:

                    print(
                        f"Could not evaluate "
                        f"{fixture['fixture']['id']}: {e}"
                    )

                # Stay safely below API rate limits.
                time.sleep(0.25)

        except Exception as e:

            print(
                f"League failed: "
                f"{league_name}: {e}"
            )

    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    live = {
        "generated_at": generated_at,
        "season": SEASON,
        "leagues": scanned_leagues,
        "qualifiers": all_qualifiers
    }

    save_json(
        LIVE_FILE,
        live
    )

    # Keep historical scanner runs.
    history = load_json(
        HISTORY_FILE,
        []
    )

    history.append({
        "generated_at": generated_at,
        "qualifiers": all_qualifiers
    })

    # Keep approximately the last 14 days.
    cutoff = now - timedelta(days=14)

    cleaned_history = []

    for item in history:

        try:
            item_date = datetime.fromisoformat(
                item["generated_at"].replace(
                    "Z",
                    "+00:00"
                )
            )

            if item_date >= cutoff:
                cleaned_history.append(item)

        except Exception:
            pass

    save_json(
        HISTORY_FILE,
        cleaned_history
    )

    print("")
    print("================================")
    print("PATTERN SCANNER COMPLETE")
    print("================================")
    print(
        f"Qualifying fixtures: "
        f"{len(all_qualifiers)}"
    )
    print(
        f"History records: "
        f"{len(cleaned_history)}"
    )


if __name__ == "__main__":
    main()
