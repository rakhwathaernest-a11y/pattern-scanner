import os
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

SEASON = 2026
PREVIOUS_SEASON = 2025

DATA_DIR = "data"
LIVE_FILE = os.path.join(DATA_DIR, "live.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

LEAGUES = {
    "eng.1": "England Premier League",
    "eng.2": "England Championship",
    "fra.1": "France Ligue 1",
    "ger.1": "Germany Bundesliga",
    "por.1": "Portugal Liga Portugal",
    "den.1": "Denmark Superliga",
    "ita.1": "Italy Serie A",
    "esp.1": "Spain LaLiga",
    "aut.1": "Austria Bundesliga",
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 pattern-scanner/1.0"
})


# ============================================================
# API
# ============================================================

def api_get(path, params=None):
    url = BASE_URL + path

    response = SESSION.get(
        url,
        params=params or {},
        timeout=30
    )

    if response.status_code == 429:
        raise RuntimeError(
            "ESPN rate limit reached (429)."
        )

    response.raise_for_status()

    return response.json()


# ============================================================
# ESPN TEAM LIST
# ============================================================

def get_teams(league_code):

    data = api_get(
        f"/{league_code}/teams"
    )

    found = []

    def walk(value):

        if isinstance(value, dict):

            team = value.get("team")

            if isinstance(team, dict):
                if team.get("id"):
                    found.append(team)

            for child in value.values():
                walk(child)

        elif isinstance(value, list):

            for child in value:
                walk(child)

    walk(data)

    unique = {}

    for team in found:

        team_id = str(team.get("id"))

        if team_id:
            unique[team_id] = team

    return list(unique.values())


# ============================================================
# TEAM SCHEDULE
# ============================================================

def get_team_schedule(
    league_code,
    team_id,
    season
):

    data = api_get(
        f"/{league_code}/teams/{team_id}/schedule",
        {
            "season": season,
            "seasontype": 2
        }
    )

    return data.get("events", [])


# ============================================================
# ESPN EVENT -> INTERNAL MATCH FORMAT
# ============================================================

def normalize_event(event):

    competitions = event.get(
        "competitions",
        []
    )

    if not competitions:
        return None

    competition = competitions[0]

    competitors = competition.get(
        "competitors",
        []
    )

    if len(competitors) < 2:
        return None

    home = None
    away = None

    for competitor in competitors:

        if competitor.get("homeAway") == "home":
            home = competitor

        elif competitor.get("homeAway") == "away":
            away = competitor

    if not home or not away:
        return None

    home_team = home.get(
        "team",
        {}
    )

    away_team = away.get(
        "team",
        {}
    )

    status = competition.get(
        "status",
        {}
    )

    status_type = status.get(
        "type",
        {}
    )

    state = status_type.get(
        "state",
        ""
    )

    status_name = status_type.get(
        "name",
        ""
    )

    if (
        state == "post"
        or status_name in {
            "STATUS_FINAL",
            "STATUS_FINAL_OT",
            "STATUS_FINAL_PEN"
        }
    ):

        normalized_status = "FINISHED"

    elif (
        state == "pre"
        or status_name == "STATUS_SCHEDULED"
    ):

        normalized_status = "SCHEDULED"

    else:

        normalized_status = "LIVE"

    def get_score(competitor):

        value = competitor.get("score")

        try:
            return int(value)

        except (
            TypeError,
            ValueError
        ):

            return None

    return {
        "id": str(
            event.get("id", "")
        ),

        "utcDate": event.get(
            "date",
            ""
        ),

        "status": normalized_status,

        "homeTeam": {
            "id": home_team.get("id"),
            "name": (
                home_team.get("displayName")
                or home_team.get("name")
                or home_team.get("shortDisplayName")
                or "Unknown"
            )
        },

        "awayTeam": {
            "id": away_team.get("id"),
            "name": (
                away_team.get("displayName")
                or away_team.get("name")
                or away_team.get("shortDisplayName")
                or "Unknown"
            )
        },

        "score": {
            "fullTime": {
                "home": get_score(home),
                "away": get_score(away)
            }
        }
    }


# ============================================================
# FETCH COMPLETE LEAGUE
# ============================================================

def fetch_league_matches(
    league_code,
    date_from,
    date_to
):

    print(
        "  Loading teams..."
    )

    teams_list = get_teams(
        league_code
    )

    if not teams_list:

        raise RuntimeError(
            "ESPN returned no teams."
        )

    print(
        "  Teams found:",
        len(teams_list)
    )

    jobs = []

    for team in teams_list:

        team_id = team.get("id")

        if not team_id:
            continue

        jobs.append(
            (
                team_id,
                PREVIOUS_SEASON
            )
        )

        jobs.append(
            (
                team_id,
                SEASON
            )
        )

    raw_events = {}

    # --------------------------------------------------------
    # Fetch team schedules
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=8
    ) as executor:

        future_map = {}

        for team_id, season in jobs:

            future = executor.submit(
                get_team_schedule,
                league_code,
                team_id,
                season
            )

            future_map[future] = (
                team_id,
                season
            )

        for future in as_completed(
            future_map
        ):

            team_id, season = future_map[
                future
            ]

            try:

                events = future.result()

            except Exception as error:

                print(
                    "  Schedule error:",
                    team_id,
                    season,
                    "|",
                    str(error)
                )

                continue

            for event in events:

                event_id = str(
                    event.get(
                        "id",
                        ""
                    )
                )

                if not event_id:
                    continue

                raw_events[
                    event_id
                ] = event

    # --------------------------------------------------------
    # Normalize and filter
    # --------------------------------------------------------

    matches = []

    for event in raw_events.values():

        match = normalize_event(
            event
        )

        if not match:
            continue

        match_date = match.get(
            "utcDate",
            ""
        )

        if not match_date:
            continue

        try:

            match_dt = datetime.fromisoformat(
                match_date.replace(
                    "Z",
                    "+00:00"
                )
            )

        except ValueError:

            continue

        if (
            date_from
            <= match_dt.date()
            <= date_to
        ):

            match["league"] = league_code

            matches.append(
                match
            )

    matches.sort(
        key=fixture_date
    )

    return matches


# ============================================================
# MATCH HELPERS
# ============================================================

def fixture_date(match):

    return match.get(
        "utcDate",
        ""
    )


def teams(match):

    home = match.get(
        "homeTeam",
        {}
    )

    away = match.get(
        "awayTeam",
        {}
    )

    return (
        home.get("id"),
        away.get("id"),
        home.get(
            "name",
            "Unknown"
        ),
        away.get(
            "name",
            "Unknown"
        )
    )


def score(match):

    full_time = match.get(
        "score",
        {}
    ).get(
        "fullTime",
        {}
    )

    return (
        full_time.get("home"),
        full_time.get("away")
    )


def is_finished(match):

    return match.get(
        "status"
    ) in {
        "FINISHED",
        "AWARDED"
    }


def result_type(match):

    home_goals, away_goals = score(
        match
    )

    if (
        home_goals is None
        or away_goals is None
    ):

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
        m
        for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(
        finished
    )

    results = []

    for match in finished[:10]:

        result = result_type(
            match
        )

        if result:

            results.append(
                result
            )

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
        m
        for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(
        finished
    )

    results = []

    for match in finished[:10]:

        result = result_type(
            match
        )

        if result:

            results.append(
                result
            )

    return (
        results[:4]
        == PATTERN_2
    )


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
        m
        for m in matches
        if is_finished(m)
    ]

    finished = sort_newest(
        finished
    )

    results = []

    for match in finished[:10]:

        result = result_type(
            match
        )

        if result:

            results.append(
                result
            )

    return (
        results[:5]
        == PATTERN_3
    )


# ============================================================
# TEAM-SPECIFIC PATTERN 1
# ============================================================

def team_result(
    match,
    team_id
):

    home_id, away_id, _, _ = teams(
        match
    )

    home_goals, away_goals = score(
        match
    )

    if (
        home_goals is None
        or away_goals is None
    ):

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
        before_date.replace(
            "Z",
            "+00:00"
        )
    )

    recent = []

    for match in matches:

        if not is_finished(match):
            continue

        match_date = fixture_date(
            match
        )

        if not match_date:
            continue

        try:

            match_dt = datetime.fromisoformat(
                match_date.replace(
                    "Z",
                    "+00:00"
                )
            )

        except ValueError:

            continue

        if match_dt >= cutoff:
            continue

        home_id, away_id, _, _ = teams(
            match
        )

        if team_id not in {
            home_id,
            away_id
        }:

            continue

        opponent_id = (
            away_id
            if team_id == home_id
            else home_id
        )

        if (
            opponent_id
            == upcoming_opponent_id
        ):

            continue

        recent.append(
            match
        )

    recent = sort_newest(
        recent
    )[:2]

    results = []

    for match in recent:

        result = team_result(
            match,
            team_id
        )

        if result:

            results.append(
                result
            )

    if results == [
        "win",
        "draw_goals"
    ]:

        return True

    if results == [
        "draw_goals",
        "win"
    ]:

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
        before_date.replace(
            "Z",
            "+00:00"
        )
    )

    h2h = []

    for match in all_matches:

        if not is_finished(match):
            continue

        match_date = fixture_date(
            match
        )

        if not match_date:
            continue

        try:

            match_dt = datetime.fromisoformat(
                match_date.replace(
                    "Z",
                    "+00:00"
                )
            )

        except ValueError:

            continue

        if match_dt >= cutoff:
            continue

        match_home_id, match_away_id, _, _ = teams(
            match
        )

        if {
            match_home_id,
            match_away_id
        } == {
            home_id,
            away_id
        }:

            h2h.append(
                match
            )

    return sort_newest(
        h2h
    )[:10]


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

    match_date = fixture_date(
        match
    )

    if (
        not home_id
        or not away_id
        or not match_date
    ):

        return None

    # --------------------------------------------------------
    # H2H
    # --------------------------------------------------------

    h2h = get_h2h(
        all_matches,
        home_id,
        away_id,
        match_date
    )

    if len(h2h) < 2:

        return None

    h2h_sequence = []

    for h2h_match in h2h:

        result = result_type(
            h2h_match
        )

        if result:

            h2h_sequence.append(
                result
            )

    print(
        "  H2H sequence:",
        " -> ".join(
            h2h_sequence
        )
        if h2h_sequence
        else "NO DATA"
    )

    # --------------------------------------------------------
    # PATTERN 1
    # --------------------------------------------------------

    pattern1_match, target = pattern1_h2h(
        h2h
    )

    if pattern1_match:

        target_team = (
            home_name
            if target == "home"
            else away_name
        )

        # ----------------------------------------------------
        # PATTERN 4
        # ----------------------------------------------------

        home_pattern1 = team_pattern1(
            all_matches,
            home_id,
            away_id,
            match_date
        )

        away_pattern1 = team_pattern1(
            all_matches,
            away_id,
            home_id,
            match_date
        )

        print(
            "  Team A recent Pattern 1:",
            "YES"
            if home_pattern1
            else "NO"
        )

        print(
            "  Team B recent Pattern 1:",
            "YES"
            if away_pattern1
            else "NO"
        )

        if (
            home_pattern1
            and away_pattern1
        ):

            print(
                "  PATTERN 4: H2H + Team A + Team B = YES"
            )

            return {
                "league": league_name,
                "league_code": league_code,
                "date": match_date,
                "home": home_name,
                "away": away_name,
                "market": "BETS",
                "target_team": target_team,
                "pattern": "Pattern 4",
                "h2h_sequence": h2h_sequence
            }

        # ----------------------------------------------------
        # NORMAL PATTERN 1
        # ----------------------------------------------------

        return {
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

def load_json(
    path,
    default
):

    if not os.path.exists(path):

        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return default


def save_json(
    path,
    data
):

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

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

    now = datetime.now(
        timezone.utc
    )

    today = now.date()

    end_date = (
        today
        + timedelta(days=7)
    )

    date_from = (
        today
        - timedelta(days=365)
    )

    all_results = []

    league_status = []

    successful_leagues = 0

    print(
        "========================================"
    )

    print(
        "FOOTBALL PATTERN SCANNER"
    )

    print(
        "========================================"
    )

    print(
        "Source: ESPN public site API"
    )

    print(
        "Scanning:",
        date_from,
        "through",
        end_date
    )

    print(
        "========================================"
    )

    # ========================================================
    # LEAGUES
    # ========================================================

    for league_code, league_name in LEAGUES.items():

        print()

        print(
            "----------------------------------------"
        )

        print(
            "League:",
            league_name
        )

        print(
            "Code:",
            league_code
        )

        print(
            "----------------------------------------"
        )

        try:

            matches = fetch_league_matches(
                league_code,
                date_from,
                end_date
            )

            print(
                "Matches returned:",
                len(matches)
            )

            if not matches:

                raise RuntimeError(
                    "ESPN returned no matches."
                )

            successful_leagues += 1

            league_status.append({
                "league": league_name,
                "code": league_code,
                "status": "OK",
                "matches": len(matches)
            })

            # ------------------------------------------------
            # UPCOMING MATCHES
            # ------------------------------------------------

            upcoming = []

            for match in matches:

                if match.get(
                    "status"
                ) != "SCHEDULED":

                    continue

                match_date = fixture_date(
                    match
                )

                if not match_date:
                    continue

                try:

                    match_dt = datetime.fromisoformat(
                        match_date.replace(
                            "Z",
                            "+00:00"
                        )
                    )

                except ValueError:

                    continue

                if match_dt < now:
                    continue

                if (
                    match_dt.date()
                    > end_date
                ):

                    continue

                upcoming.append(
                    match
                )

            upcoming.sort(
                key=lambda x: fixture_date(x)
            )

            print(
                "Upcoming matches:",
                len(upcoming)
            )

            # ------------------------------------------------
            # CHECK EACH UPCOMING MATCH
            # ------------------------------------------------

            for match in upcoming:

                (
                    home_id,
                    away_id,
                    home_name,
                    away_name
                ) = teams(match)

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

                        all_results.append(
                            result
                        )

                    else:

                        print(
                            "  No pattern"
                        )

                except Exception as match_error:

                    print(
                        "  Match error:",
                        str(match_error)
                    )

        except Exception as league_error:

            print(
                "FAILED:",
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

    # ========================================================
    # SAFETY CHECK
    # ========================================================

    if (
        successful_leagues
        != len(LEAGUES)
    ):

        raise RuntimeError(
            f"Scan incomplete: "
            f"{successful_leagues}/"
            f"{len(LEAGUES)} leagues succeeded. "
            "Existing live.json was not replaced."
        )

    # ========================================================
    # SORT RESULTS
    # ========================================================

    all_results.sort(
        key=lambda x: x.get(
            "date",
            ""
        )
    )

    # ========================================================
    # LIVE OUTPUT
    # ========================================================

    output = {

        "updated_at":
            now.isoformat(),

        "season":
            SEASON,

        "date_from":
            str(today),

        "date_to":
            str(end_date),

        "matches":
            all_results,

        "league_status":
            league_status
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

    if not isinstance(
        history,
        list
    ):

        history = []

    history.append(
        output
    )

    cutoff_history = (
        now
        - timedelta(days=14)
    )

    cleaned_history = []

    for item in history:

        timestamp = item.get(
            "updated_at"
        )

        if not timestamp:
            continue

        try:

            item_dt = datetime.fromisoformat(
                timestamp.replace(
                    "Z",
                    "+00:00"
                )
            )

            if item_dt >= cutoff_history:

                cleaned_history.append(
                    item
                )

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

    print(
        "========================================"
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        "========================================"
    )

    print(
        "Successful leagues:",
        successful_leagues
    )

    print(
        "Pattern matches:",
        len(all_results)
    )

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

    print(
        "========================================"
    )


if __name__ == "__main__":
    main()