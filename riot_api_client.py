import asyncio
import datetime
import csv
import os
from aiohttp import ClientSession
from aiolimiter import AsyncLimiter

###############################################################################
# 1. KONFIGURACJA
###############################################################################
RIOT_API_KEY = "RGAPI-8f0faf67-eeab-4da0-b629-f3c918cfaa38"  # <-- Twój klucz
MATCH_REGION_BASE_URL = "https://europe.api.riotgames.com"   # Dla PUUID z EU
BASE_DOMAIN = "eun1.api.riotgames.com"

CHUNK_SIZE = 50         # Co ile wierszy tworzymy NOWY plik CSV
MAX_ROWS = 200000           # Ile łącznie wierszy chcemy pobrać
MATCH_HISTORY_COUNT = 20 # Ile meczów pobieramy na 1 puuid w get_match_history

# Asynchroniczne ograniczenie do ~15 RPS (bez spamu w konsoli)
RATE_LIMIT = AsyncLimiter(15, 1.0)

HEADERS = {
    "X-Riot-Token": RIOT_API_KEY
}

PLATFORM_MAP = {
    "EUW1": "euw1.api.riotgames.com",
    "EUN1": "eun1.api.riotgames.com",
    "NA1":  "na1.api.riotgames.com",
    "KR":   "kr.api.riotgames.com",
    "TR1":  "tr1.api.riotgames.com",
    "RU":   "ru.api.riotgames.com",
    "BR1":  "br1.api.riotgames.com",
    "LA1":  "la1.api.riotgames.com",
    "LA2":  "la2.api.riotgames.com",
    "OC1":  "oc1.api.riotgames.com",
}

###############################################################################
# 2. CACHE
###############################################################################
match_details_cache = {}
match_timeline_cache = {}
summoner_rank_cache = {}
champion_mastery_cache = {}

###############################################################################
# 3. do_request - asynchroniczne zapytanie
###############################################################################
async def do_request(session: ClientSession, url: str, method="GET", params=None, headers=None, retries=0, max_retries=5):
    """
    Asynchroniczne zapytanie HTTP z ograniczeniem RPS (AsyncLimiter),
    obsługą 429 i 5xx.
    """
    if headers is None:
        headers = {}
    if retries > max_retries:
        print(f"[ERROR] Przekroczono limit prób ({max_retries}) dla URL: {url}")
        return None

    async with RATE_LIMIT:
        try:
            if method == "GET":
                resp = await session.get(url, params=params, headers=headers)
            else:
                resp = await session.request(method, url, params=params, headers=headers)
        except Exception as e:
            print(f"[WARN] Wyjątek {e} (URL: {url}) - ponawiam za 2s...")
            await asyncio.sleep(2)
            return await do_request(session, url, method, params, headers, retries=retries+1)

    if resp.status == 200:
        return resp
    elif resp.status == 429:
        retry_after = int(resp.headers.get("Retry-After", 1))
        print(f"[429] Osiągnięto limit. Czekam {retry_after}s (URL: {url})")
        await asyncio.sleep(retry_after)
        return await do_request(session, url, method, params, headers, retries=retries+1)
    elif resp.status in [500, 502, 503, 504]:
        print(f"[{resp.status}] Błąd serwera. Czekam 5s (URL: {url})")
        await asyncio.sleep(5)
        return await do_request(session, url, method, params, headers, retries=retries+1)
    else:
        text = await resp.text()
        print(f"[{resp.status}] {text} (URL: {url})")
        return None

###############################################################################
# 4. FUNKCJE DO POBIERANIA DANYCH
###############################################################################
async def get_match_history(session, puuid, count=MATCH_HISTORY_COUNT):
    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/by-puuid/{puuid}/ids"
    params = {"count": count}
    resp = await do_request(session, url, "GET", params=params, headers=HEADERS)
    if resp:
        return await resp.json()
    return []

async def get_match_details(session, match_id):
    if match_id in match_details_cache:
        return match_details_cache[match_id]

    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/{match_id}"
    resp = await do_request(session, url, "GET", headers=HEADERS)
    if resp:
        data = await resp.json()
        match_details_cache[match_id] = data
        return data
    return None

async def get_match_timeline(session, match_id):
    if match_id in match_timeline_cache:
        return match_timeline_cache[match_id]

    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/{match_id}/timeline"
    resp = await do_request(session, url, "GET", headers=HEADERS)
    if resp:
        data = await resp.json()
        match_timeline_cache[match_id] = data
        return data
    return None

async def get_summoner_rank(session, summoner_id, platform_id):
    if not summoner_id or not platform_id:
        return {}
    cache_key = f"{platform_id}:{summoner_id}"
    if cache_key in summoner_rank_cache:
        return summoner_rank_cache[cache_key]

    base_domain = PLATFORM_MAP.get(platform_id.upper(), BASE_DOMAIN)
    url = f"https://{base_domain}/lol/league/v4/entries/by-summoner/{summoner_id}"
    resp = await do_request(session, url, "GET", headers=HEADERS)

    rank_info = {
        "solo_tier": None, "solo_rank": None, "solo_lp": None,
        "solo_wins": None, "solo_losses": None,
        "flex_tier": None, "flex_rank": None, "flex_lp": None,
        "flex_wins": None, "flex_losses": None,
    }
    if resp:
        data = await resp.json()
        for entry in data:
            q_type = entry.get("queueType")
            if q_type == "RANKED_SOLO_5x5":
                rank_info["solo_tier"] = entry.get("tier")
                rank_info["solo_rank"] = entry.get("rank")
                rank_info["solo_lp"]   = entry.get("leaguePoints")
                rank_info["solo_wins"] = entry.get("wins")
                rank_info["solo_losses"] = entry.get("losses")
            elif q_type == "RANKED_FLEX_SR":
                rank_info["flex_tier"] = entry.get("tier")
                rank_info["flex_rank"] = entry.get("rank")
                rank_info["flex_lp"]   = entry.get("leaguePoints")
                rank_info["flex_wins"] = entry.get("wins")
                rank_info["flex_losses"] = entry.get("losses")

    summoner_rank_cache[cache_key] = rank_info
    return rank_info

async def get_champion_mastery(session, puuid, champion_id):
    if not puuid or champion_id is None:
        return {
            "champion_mastery_level": None,
            "champion_mastery_points": None,
            "champion_mastery_lastPlayTime": None,
            "champion_mastery_pointsSinceLastLevel": None,
            "champion_mastery_pointsUntilNextLevel": None,
            "champion_mastery_tokensEarned": None,
        }

    if puuid not in champion_mastery_cache:
        url = f"https://{BASE_DOMAIN}/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
        resp = await do_request(session, url, "GET", headers=HEADERS)
        mastery_dict = {}
        if resp:
            mastery_list = await resp.json()
            for item in mastery_list:
                c_id = item.get("championId")
                mastery_dict[c_id] = {
                    "champion_mastery_level": item.get("championLevel"),
                    "champion_mastery_points": item.get("championPoints"),
                    "champion_mastery_lastPlayTime": item.get("lastPlayTime"),
                    "champion_mastery_pointsSinceLastLevel": item.get("championPointsSinceLastLevel"),
                    "champion_mastery_pointsUntilNextLevel": item.get("championPointsUntilNextLevel"),
                    "champion_mastery_tokensEarned": item.get("tokensEarned"),
                }
        champion_mastery_cache[puuid] = mastery_dict

    return champion_mastery_cache[puuid].get(champion_id, {
        "champion_mastery_level": None,
        "champion_mastery_points": None,
        "champion_mastery_lastPlayTime": None,
        "champion_mastery_pointsSinceLastLevel": None,
        "champion_mastery_pointsUntilNextLevel": None,
        "champion_mastery_tokensEarned": None,
    })

###############################################################################
# 5. FINAL CHAMPION STATS
###############################################################################
def get_final_champion_stats(timeline_data, participant_id):
    result = {}
    if not timeline_data:
        return result

    info = timeline_data.get("info", {})
    frames = info.get("frames", [])
    if not frames:
        return result

    last_frame = frames[-1]
    participant_frames = last_frame.get("participantFrames", {})
    p_key = str(participant_id)
    frame_data = participant_frames.get(p_key, {})
    champ_stats = frame_data.get("championStats", {})

    for field in [
        "abilityHaste","abilityPower","armor","armorPen","armorPenPercent",
        "attackDamage","attackSpeed","bonusArmorPenPercent","bonusMagicPenPercent",
        "ccReduction","cooldownReduction","health","healthMax","healthRegen",
        "lifesteal","magicPen","magicPenPercent","magicResist","movementSpeed",
        "omnivamp","physicalVamp","power","powerMax","powerRegen","spellVamp"
    ]:
        val = champ_stats.get(field, None)
        result[f"final_{field}"] = val
    return result

###############################################################################
# 6. PRZETWARZANIE DANYCH
###############################################################################
async def process_match_data(session, match_data, timeline_data, puuid_pool):
    if not match_data:
        return []

    info = match_data["info"]
    participants = info.get("participants", [])
    platform_id = info.get("platformId")

    keep_game_id = info.get("gameId")
    keep_game_duration = info.get("gameDuration")
    keep_game_mode = info.get("gameMode")
    keep_game_type = info.get("gameType")
    keep_game_version = info.get("gameVersion")
    keep_map_id = info.get("mapId")
    keep_queue_id = info.get("queueId")

    timestamp_ms = info.get("gameStartTimestamp")
    if timestamp_ms:
        dt_utc = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
        game_start_utc = dt_utc.isoformat() + "Z"
    else:
        game_start_utc = None

    rows = []
    for part in participants:
        p = part.get("puuid")
        if p:
            puuid_pool.add(p)

        summoner_id = part.get("summonerId")
        rank_data = await get_summoner_rank(session, summoner_id, platform_id)

        champion_id = part.get("championId")
        mastery_data = await get_champion_mastery(session, p, champion_id)

        # Konwersja champion_mastery_lastPlayTime -> int
        raw_last_play = mastery_data.get("champion_mastery_lastPlayTime")
        if isinstance(raw_last_play, float):
            raw_last_play = int(raw_last_play)

        # Ewentualna konwersja do daty
        if raw_last_play:
            dt_lp = datetime.datetime.utcfromtimestamp(raw_last_play / 1000.0)
            champion_mastery_lastPlayTime_utc = dt_lp.isoformat() + "Z"
        else:
            champion_mastery_lastPlayTime_utc = None

        final_stats = get_final_champion_stats(timeline_data, part.get("participantId"))

        row_data = {
            "game_id": keep_game_id,
            "game_start_utc": game_start_utc,
            "game_duration": keep_game_duration,
            "game_mode": keep_game_mode,
            "game_type": keep_game_type,
            "game_version": keep_game_version,
            "map_id": keep_map_id,
            "platform_id": platform_id,
            "queue_id": keep_queue_id,

            "participant_id": part.get("participantId"),
            "puuid": p,
            "summoner_name": part.get("summonerName"),
            "summoner_id": summoner_id,
            "summoner_level": part.get("summonerLevel"),
            "champion_id": champion_id,
            "champion_name": part.get("championName"),
            "team_id": part.get("teamId"),
            "win": part.get("win"),

            "individual_position": part.get("individualPosition"),
            "team_position": part.get("teamPosition"),
            "lane": part.get("lane"),
            "role": part.get("role"),

            "kills": part.get("kills"),
            "deaths": part.get("deaths"),
            "assists": part.get("assists"),
            "baron_kills": part.get("baronKills"),
            "dragon_kills": part.get("dragonKills"),
            "gold_earned": part.get("goldEarned"),
            "gold_spent": part.get("goldSpent"),
            "total_damage_dealt": part.get("totalDamageDealt"),
            "total_damage_dealt_to_champions": part.get("totalDamageDealtToChampions"),
            "physical_damage_dealt_to_champions": part.get("physicalDamageDealtToChampions"),
            "magic_damage_dealt_to_champions": part.get("magicDamageDealtToChampions"),
            "true_damage_dealt_to_champions": part.get("trueDamageDealtToChampions"),
            "damage_dealt_to_objectives": part.get("damageDealtToObjectives"),
            "damage_dealt_to_turrets": part.get("damageDealtToTurrets"),
            "total_damage_taken": part.get("totalDamageTaken"),
            "physical_damage_taken": part.get("physicalDamageTaken"),
            "magic_damage_taken": part.get("magicDamageTaken"),
            "true_damage_taken": part.get("trueDamageTaken"),
            "time_ccing_others": part.get("timeCCingOthers"),
            "vision_score": part.get("visionScore"),
            "wards_placed": part.get("wardsPlaced"),
            "wards_killed": part.get("wardsKilled"),
            "vision_wards_bought_in_game": part.get("visionWardsBoughtInGame"),

            "item0": part.get("item0"),
            "item1": part.get("item1"),
            "item2": part.get("item2"),
            "item3": part.get("item3"),
            "item4": part.get("item4"),
            "item5": part.get("item5"),
            "item6": part.get("item6"),

            "solo_tier": rank_data.get("solo_tier"),
            "solo_rank": rank_data.get("solo_rank"),
            "solo_lp":   rank_data.get("solo_lp"),
            "solo_wins": rank_data.get("solo_wins"),
            "solo_losses": rank_data.get("solo_losses"),
            "flex_tier": rank_data.get("flex_tier"),
            "flex_rank": rank_data.get("flex_rank"),
            "flex_lp":   rank_data.get("flex_lp"),
            "flex_wins": rank_data.get("flex_wins"),
            "flex_losses": rank_data.get("flex_losses"),

            "champion_mastery_level": mastery_data.get("champion_mastery_level"),
            "champion_mastery_points": mastery_data.get("champion_mastery_points"),
            "champion_mastery_lastPlayTime": raw_last_play,
            "champion_mastery_lastPlayTime_utc": champion_mastery_lastPlayTime_utc,
            "champion_mastery_pointsSinceLastLevel": mastery_data.get("champion_mastery_pointsSinceLastLevel"),
            "champion_mastery_pointsUntilNextLevel": mastery_data.get("champion_mastery_pointsUntilNextLevel"),
            "champion_mastery_tokensEarned": mastery_data.get("champion_mastery_tokensEarned"),
        }
        row_data.update(final_stats)
        rows.append(row_data)

    return rows

###############################################################################
# 7. ZAPISYWANIE CHUNKAMI Z USUWANIEM POPRZEDNIEGO
###############################################################################
def save_chunk_to_csv(all_data, total_rows):
    """
    Tworzy plik league_data_{total_rows}.csv z CAŁĄ dotychczasową listą all_data,
    a następnie usuwa plik poprzedniego chunku (league_data_{total_rows-CHUNK_SIZE}.csv).
    """
    if not all_data:
        return

    row_count = len(all_data)  # w pamięci
    filename = f"league_data_{total_rows}.csv"
    keys = all_data[0].keys()

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(all_data)

    print(f"[SAVE] Zapisano {row_count} wierszy (cumulative) do pliku: {filename}")

    # Usunięcie starego chunku
    prev_count = total_rows - CHUNK_SIZE
    if prev_count > 0:
        prev_filename = f"league_data_{prev_count}.csv"
        if os.path.exists(prev_filename):
            os.remove(prev_filename)
            print(f"Usunięto poprzedni plik: {prev_filename}")

###############################################################################
# 8. GŁÓWNA FUNKCJA
###############################################################################
async def main():
    initial_puuid = "1KsVizuCBGvjF6QwkrfiQ0vrukMNZHAMw7t8fqn-knBGm3fmAsGunqCRn17q3ipY63Re8Y-ZkHIYaw"
    puuid_pool = {initial_puuid}
    processed_matches = set()

    all_data = []
    total_rows = 0
    rows_since_last_save = 0

    async with ClientSession() as session:
        while total_rows < MAX_ROWS and puuid_pool:
            current_puuid = puuid_pool.pop()
            print(f"[INFO] Pobieram historię meczów dla PUUID: {current_puuid}")
            match_ids = await get_match_history(session, current_puuid, count=MATCH_HISTORY_COUNT)

            if not match_ids:
                print(f"[WARN] Brak match_ids dla {current_puuid} lub błąd przy pobieraniu.")
                continue

            for match_id in match_ids:
                if match_id in processed_matches:
                    continue

                print(f"[INFO] -> Szczegóły meczu {match_id}")
                match_details = await get_match_details(session, match_id)
                if match_details:
                    processed_matches.add(match_id)

                    print(f"[INFO] -> Timeline meczu {match_id}")
                    timeline = await get_match_timeline(session, match_id)

                    new_rows = await process_match_data(session, match_details, timeline, puuid_pool)
                    for row in new_rows:
                        all_data.append(row)
                        total_rows += 1
                        rows_since_last_save += 1

                        print(f"Przetworzono łącznie {total_rows} wierszy.")

                        # Po osiągnięciu CHUNK_SIZE -> zapis pliku
                        if rows_since_last_save >= CHUNK_SIZE:
                            save_chunk_to_csv(all_data, total_rows)
                            # Nie czyścimy all_data - chcemy plik cumulative
                            rows_since_last_save = 0

                        if total_rows >= MAX_ROWS:
                            print("[INFO] Osiągnięto limit MAX_ROWS.")
                            break

                if total_rows >= MAX_ROWS:
                    break

    # Jeśli został niedomknięty chunk
    if all_data and (total_rows % CHUNK_SIZE != 0):
        save_chunk_to_csv(all_data, total_rows)

    print("[DONE] Koniec zbierania danych.")
    print(f"Zebrano łącznie {total_rows} wierszy.")


###############################################################################
# 9. START
###############################################################################
if __name__ == "__main__":
    asyncio.run(main())
