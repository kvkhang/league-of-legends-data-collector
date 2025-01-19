import requests
import csv
import time
import os

###############################################################################
# 1. KONFIGURACJA
###############################################################################
RIOT_API_KEY = "RGAPI-8f0faf67-eeab-4da0-b629-f3c918cfaa38"  # <-- Twój klucz
MATCH_REGION_BASE_URL = "https://europe.api.riotgames.com"  # Dla PUUID z EU
BASE_DOMAIN = "eun1.api.riotgames.com"

HEADERS = {
    "X-Riot-Token": RIOT_API_KEY
}

# Mapa platformId -> domeny do League-V4 / Summoner-V4 / Champion Mastery V4
PLATFORM_MAP = {
    "EUW1": "euw1.api.riotgames.com",
    "EUN1": "eun1.api.riotgames.com",
    "NA1": "na1.api.riotgames.com",
    "KR": "kr.api.riotgames.com",
    "TR1": "tr1.api.riotgames.com",
    "RU": "ru.api.riotgames.com",
    "BR1": "br1.api.riotgames.com",
    "LA1": "la1.api.riotgames.com",
    "LA2": "la2.api.riotgames.com",
    "OC1": "oc1.api.riotgames.com",
    # Dodaj w razie potrzeby
}

RETRIES_LIMIT = 5  # Ile razy ponawiamy zapytanie HTTP
CHUNK_SIZE = 100  # Co ile nowych wierszy zapisujemy plik CSV
MAX_ROWS = 100000  # Ile łącznie rekordów chcemy zebrać


###############################################################################
# 2. FUNKCJA: do_request - uniwersalne zapytanie z retry
###############################################################################
def do_request(url, method="GET", params=None, headers=None, retries=0):
    """
    Wykonuje zapytanie HTTP z obsługą:
    - 429 (limit zapytań) -> odczekanie Retry-After i ponowienie
    - 5xx (błąd serwera) -> krótka pauza i ponowienie
    - limit ponowień (RETRIES_LIMIT)
    """
    if headers is None:
        headers = {}
    if retries > RETRIES_LIMIT:
        print(f"Przekroczono maksymalną liczbę prób ({RETRIES_LIMIT}). URL: {url}")
        return None

    try:
        if method == "GET":
            resp = requests.get(url, params=params, headers=headers)
        else:
            resp = requests.request(method, url, params=params, headers=headers)

        if resp.status_code == 200:
            return resp
        elif resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 1))
            print(f"HTTP 429 - limit zapytań. Czekam {retry_after}s. URL: {url}")
            time.sleep(retry_after)
            return do_request(url, method, params, headers, retries=retries + 1)
        elif resp.status_code in [500, 502, 503, 504]:
            print(f"HTTP {resp.status_code} - Błąd serwera. Czekam 5s. URL: {url}")
            time.sleep(5)
            return do_request(url, method, params, headers, retries=retries + 1)
        else:
            print(f"HTTP {resp.status_code}: {resp.text} (URL: {url})")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Wyjątek requests: {e}. URL: {url}")
        time.sleep(2)
        return do_request(url, method, params, headers, retries=retries + 1)


###############################################################################
# 3. CACHE - słowniki w pamięci
###############################################################################

# 3a. Match Details i Timeline - żeby nie pobierać wielokrotnie tego samego meczu
match_details_cache = {}
match_timeline_cache = {}

# 3b. Rangi Summonerów
summoner_rank_cache = {}

# 3c. Champion Mastery – klucz to puuid, wartość: dict championId → mastery
champion_mastery_cache = {}


###############################################################################
# 4. FUNKCJE DO POBIERANIA DANYCH (Match, Timeline, Rangi, Mastery)
###############################################################################
def get_match_history(puuid, count=10):
    """
    Pobiera listę ID meczów (str) dla danego PUUID z region-based (Match-V5).
    """
    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/by-puuid/{puuid}/ids"
    params = {"count": count}
    resp = do_request(url, "GET", params=params, headers=HEADERS)
    if resp and resp.status_code == 200:
        return resp.json()  # lista match_id
    return []


def get_match_details(match_id):
    """
    Pobiera pełne szczegóły meczu z region-based (Match-V5).
    Korzysta z cache, by unikać wielokrotnego pobierania tego samego meczu.
    """
    if match_id in match_details_cache:
        return match_details_cache[match_id]

    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/{match_id}"
    resp = do_request(url, headers=HEADERS)
    if resp and resp.status_code == 200:
        data = resp.json()
        match_details_cache[match_id] = data
        return data
    return None


def get_match_timeline(match_id):
    """
    Pobiera timeline meczu z region-based (Match-V5).
    Również korzysta z cache.
    """
    if match_id in match_timeline_cache:
        return match_timeline_cache[match_id]

    url = f"{MATCH_REGION_BASE_URL}/lol/match/v5/matches/{match_id}/timeline"
    resp = do_request(url, headers=HEADERS)
    if resp and resp.status_code == 200:
        data = resp.json()
        match_timeline_cache[match_id] = data
        return data
    return None


def get_summoner_rank(summoner_id, platform_id):
    """
    Pobiera informacje o randze (SoloQ, Flex) z League-V4 (platform-based).
    Zwraca słownik z polami: solo_tier, solo_rank, itp.
    Cache w słowniku summoner_rank_cache.
    """
    if not summoner_id or not platform_id:
        return {}

    cache_key = f"{platform_id}:{summoner_id}"
    if cache_key in summoner_rank_cache:
        return summoner_rank_cache[cache_key]

    base_domain = PLATFORM_MAP.get(platform_id.upper(), BASE_DOMAIN)
    url = f"https://{base_domain}/lol/league/v4/entries/by-summoner/{summoner_id}"
    resp = do_request(url, headers=HEADERS)
    rank_info = {
        "solo_tier": None, "solo_rank": None, "solo_lp": None,
        "solo_wins": None, "solo_losses": None,
        "flex_tier": None, "flex_rank": None, "flex_lp": None,
        "flex_wins": None, "flex_losses": None,
    }
    if resp and resp.status_code == 200:
        data = resp.json()  # lista
        for entry in data:
            q_type = entry.get("queueType")
            if q_type == "RANKED_SOLO_5x5":
                rank_info["solo_tier"] = entry.get("tier")
                rank_info["solo_rank"] = entry.get("rank")
                rank_info["solo_lp"] = entry.get("leaguePoints")
                rank_info["solo_wins"] = entry.get("wins")
                rank_info["solo_losses"] = entry.get("losses")
            elif q_type == "RANKED_FLEX_SR":
                rank_info["flex_tier"] = entry.get("tier")
                rank_info["flex_rank"] = entry.get("rank")
                rank_info["flex_lp"] = entry.get("leaguePoints")
                rank_info["flex_wins"] = entry.get("wins")
                rank_info["flex_losses"] = entry.get("losses")

    # Zapis do cache
    summoner_rank_cache[cache_key] = rank_info
    return rank_info


def get_champion_mastery(puuid, champion_id):
    """
    Pobiera champion mastery dla (puuid, championId) JEDNYM zapytaniem
    o całą listę, a następnie cache'uje.
    """
    if not puuid or champion_id is None:
        return {
            "champion_mastery_level": None,
            "champion_mastery_points": None,
            "champion_mastery_lastPlayTime": None,
            "champion_mastery_pointsSinceLastLevel": None,
            "champion_mastery_pointsUntilNextLevel": None,
            "champion_mastery_tokensEarned": None,
        }

    # Jeśli w cache nie ma jeszcze wpisu dla danego puuid, pobieramy całą listę naraz
    if puuid not in champion_mastery_cache:
        base_domain = BASE_DOMAIN
        url = f"https://{base_domain}/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
        resp = do_request(url, headers=HEADERS)
        mastery_dict = {}
        if resp and resp.status_code == 200:
            mastery_list = resp.json()  # lista obiektów {championId, championLevel, ...}
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
        # Zapisujemy do cache
        champion_mastery_cache[puuid] = mastery_dict

    # Teraz w champion_mastery_cache[puuid] mamy słownik {championId: dane}
    return champion_mastery_cache[puuid].get(champion_id, {
        "champion_mastery_level": None,
        "champion_mastery_points": None,
        "champion_mastery_lastPlayTime": None,
        "champion_mastery_pointsSinceLastLevel": None,
        "champion_mastery_pointsUntilNextLevel": None,
        "champion_mastery_tokensEarned": None,
    })


###############################################################################
# 5. Pobieranie finalnych championStats z timeline (ostatnia ramka)
###############################################################################
def get_final_champion_stats(timeline_data, participant_id):
    """
    Zwraca championStats z ostatniej klatki timeline dla gracza o participantId.
    (Zwracamy dict z polami abilityHaste, armor, etc. z prefixem "final_".)
    """
    result = {}
    if not timeline_data:
        return result

    info = timeline_data.get("info", {})
    frames = info.get("frames", [])
    if not frames:
        return result

    # Ostatnia ramka
    last_frame = frames[-1]
    participant_frames = last_frame.get("participantFrames", {})

    p_key = str(participant_id)
    frame_data = participant_frames.get(p_key, {})
    champ_stats = frame_data.get("championStats", {})

    for field in [
        "abilityHaste", "abilityPower", "armor", "armorPen", "armorPenPercent",
        "attackDamage", "attackSpeed", "bonusArmorPenPercent", "bonusMagicPenPercent",
        "ccReduction", "cooldownReduction", "health", "healthMax", "healthRegen",
        "lifesteal", "magicPen", "magicPenPercent", "magicResist", "movementSpeed",
        "omnivamp", "physicalVamp", "power", "powerMax", "powerRegen", "spellVamp"
    ]:
        val = champ_stats.get(field, None)
        result[f"final_{field}"] = val

    return result


###############################################################################
# 6. PRZETWARZANIE DANYCH MECZU (usuwamy zbędne pola)
###############################################################################
def process_match_data(match_data, timeline_data, puuid_pool):
    """
    Zwraca listę wierszy (dict). Usuwamy niechciane pola, dodajemy final championStats itd.
    """
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

    rows = []
    for part in participants:
        p = part.get("puuid")
        if p and p not in puuid_pool:
            puuid_pool.add(p)

        summoner_id = part.get("summonerId")
        rank_data = get_summoner_rank(summoner_id, platform_id)

        champion_id = part.get("championId")
        mastery_data = get_champion_mastery(p, champion_id)

        participant_id = part.get("participantId")
        final_stats = get_final_champion_stats(timeline_data, participant_id)

        row_data = {
            # Pola meczu
            "game_id": keep_game_id,
            "game_duration": keep_game_duration,
            "game_mode": keep_game_mode,
            "game_type": keep_game_type,
            "game_version": keep_game_version,
            "map_id": keep_map_id,
            "platform_id": platform_id,
            "queue_id": keep_queue_id,

            # Uczestnik
            "participant_id": participant_id,
            "puuid": p,
            "summoner_name": part.get("summonerName"),
            "summoner_id": summoner_id,
            "summoner_level": part.get("summonerLevel"),
            "champion_id": champion_id,
            "champion_name": part.get("championName"),
            "team_id": part.get("teamId"),
            "win": part.get("win"),

            # Pozycja
            "individual_position": part.get("individualPosition"),
            "team_position": part.get("teamPosition"),
            "lane": part.get("lane"),
            "role": part.get("role"),

            # K/D/A
            "kills": part.get("kills"),
            "deaths": part.get("deaths"),
            "assists": part.get("assists"),

            # Różne staty
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

            # Itemy
            "item0": part.get("item0"),
            "item1": part.get("item1"),
            "item2": part.get("item2"),
            "item3": part.get("item3"),
            "item4": part.get("item4"),
            "item5": part.get("item5"),
            "item6": part.get("item6"),

            # Rangi
            "solo_tier": rank_data.get("solo_tier"),
            "solo_rank": rank_data.get("solo_rank"),
            "solo_lp": rank_data.get("solo_lp"),
            "solo_wins": rank_data.get("solo_wins"),
            "solo_losses": rank_data.get("solo_losses"),

            "flex_tier": rank_data.get("flex_tier"),
            "flex_rank": rank_data.get("flex_rank"),
            "flex_lp": rank_data.get("flex_lp"),
            "flex_wins": rank_data.get("flex_wins"),
            "flex_losses": rank_data.get("flex_losses"),

            # Champion Mastery
            "champion_mastery_level": mastery_data.get("champion_mastery_level"),
            "champion_mastery_points": mastery_data.get("champion_mastery_points"),
            "champion_mastery_lastPlayTime": mastery_data.get("champion_mastery_lastPlayTime"),
            "champion_mastery_pointsSinceLastLevel": mastery_data.get("champion_mastery_pointsSinceLastLevel"),
            "champion_mastery_pointsUntilNextLevel": mastery_data.get("champion_mastery_pointsUntilNextLevel"),
            "champion_mastery_tokensEarned": mastery_data.get("champion_mastery_tokensEarned"),
        }

        # Staty z ostatniej klatki timeline
        row_data.update(final_stats)
        rows.append(row_data)

    return rows


###############################################################################
# 7. Zapisywanie chunków do CSV (league_data_{liczba_wierszy}.csv)
###############################################################################
def save_chunk_to_csv(data_list):
    """
    Zapisuje WSZYSTKIE dane (data_list) do pliku CSV.
    Nazwa pliku: league_data_{len(data_list)}.csv
    Usuwa poprzedni plik (league_data_{len(data_list)-CHUNK_SIZE}.csv), jeśli istnieje.
    """
    if not data_list:
        print("Brak danych do zapisania - pomijam.")
        return

    row_count = len(data_list)
    filename = f"league_data_{row_count}.csv"
    keys = data_list[0].keys()

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(data_list)

    print(f"[SAVE] Zapisano {row_count} wierszy do pliku: {filename}")

    prev_count = row_count - CHUNK_SIZE
    if prev_count > 0:
        prev_filename = f"league_data_{prev_count}.csv"
        if os.path.exists(prev_filename):
            os.remove(prev_filename)
            print(f"Usunięto poprzedni plik: {prev_filename}")


###############################################################################
# 8. GŁÓWNA LOGIKA - PĘTLA POBIERANIA
###############################################################################
if __name__ == "__main__":
    # Zaczątkowy PUUID (np. Twój)
    initial_puuid = "1KsVizuCBGvjF6QwkrfiQ0vrukMNZHAMw7t8fqn-knBGm3fmAsGunqCRn17q3ipY63Re8Y-ZkHIYaw"
    puuid_pool = {initial_puuid}
    processed_matches = set()
    all_data = []

    total_rows = 0
    rows_since_last_save = 0

    while total_rows < MAX_ROWS and puuid_pool:
        current_puuid = puuid_pool.pop()
        print(f"[INFO] Pobieram historię meczów dla PUUID: {current_puuid}")
        match_ids = get_match_history(current_puuid, count=10)

        for match_id in match_ids:
            if match_id in processed_matches:
                continue  # unikamy ponownego pobierania tego samego meczu

            print(f"  -> Szczegóły meczu {match_id}")
            match_details = get_match_details(match_id)
            if match_details:
                processed_matches.add(match_id)

                # Pobieramy timeline
                print(f"  -> Timeline meczu {match_id}")
                timeline = get_match_timeline(match_id)

                new_rows = process_match_data(match_details, timeline, puuid_pool)
                for row in new_rows:
                    all_data.append(row)
                    total_rows += 1
                    rows_since_last_save += 1
                    print(f"Przetworzono łącznie {total_rows} wierszy.")

                    if rows_since_last_save >= CHUNK_SIZE:
                        save_chunk_to_csv(all_data)
                        rows_since_last_save = 0

                    if total_rows >= MAX_ROWS:
                        break

            # Zostawiamy krótki sleep(1) – minimalna przerwa
            time.sleep(1)

            if total_rows >= MAX_ROWS:
                break

    # Jeśli zostały wiersze < CHUNK_SIZE
    if rows_since_last_save > 0:
        save_chunk_to_csv(all_data)

    print("[DONE] Koniec zbierania danych.")
    print(f"Zebrano łącznie {total_rows} wierszy.")
