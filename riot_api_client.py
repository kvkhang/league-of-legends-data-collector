import requests
import csv
import time

# Twój klucz API
RIOT_API_KEY = "RGAPI-e7cbc798-98fe-4ca7-a96b-4a997dbacb34"
MATCH_URL = "https://europe.api.riotgames.com/lol/match/v5/matches"

HEADERS = {
    "X-Riot-Token": RIOT_API_KEY
}

# Funkcja: Pobierz historię meczów na podstawie PUUID
def get_match_history(puuid, count=10):
    url = f"{MATCH_URL}/by-puuid/{puuid}/ids?count={count}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Błąd {response.status_code}: {response.text}")
        return []

# Funkcja: Pobierz szczegóły meczu
def get_match_details(match_id):
    url = f"{MATCH_URL}/{match_id}"
    response = requests.get(url, headers=HEADERS)
    try:
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:  # Rate limit exceeded
            retry_after = int(response.headers.get("Retry-After", 1))
            print(f"Limit zapytań przekroczony. Ponowne próby za {retry_after} sekund.")
            time.sleep(retry_after)
            return get_match_details(match_id)  # Ponów próbę po czasie
        elif response.status_code in [500, 502, 503, 504]:  # Problemy serwera
            print(f"Błąd serwera: {response.status_code}. Ponawiam próbę...")
            time.sleep(5)
            return get_match_details(match_id)  # Ponów próbę
        else:
            print(f"Błąd {response.status_code}: {response.text}")
            return None
    except requests.exceptions.JSONDecodeError:
        print(f"Błąd: Serwer zwrócił nieprawidłową odpowiedź dla meczu {match_id}.")
        return None

# Funkcja: Przetwarzanie danych meczu
def process_match_data(match_data, puuid_pool):
    try:
        match_info = match_data.get("info", {})
        participants = match_info.get("participants", [])
        processed_data = []

        for participant in participants:
            # Dodaj nowych graczy do puli PUUID
            if participant.get("puuid") not in puuid_pool:
                puuid_pool.add(participant.get("puuid"))

            # Zapisz szczegółowe dane gracza
            processed_data.append({
                "match_id": match_info.get("gameId"),
                "game_duration": match_info.get("gameDuration"),
                "queue_id": match_info.get("queueId"),
                "summoner_name": participant.get("summonerName"),
                "team_id": participant.get("teamId"),
                "champion_name": participant.get("championName"),
                "kills": participant.get("kills"),
                "deaths": participant.get("deaths"),
                "assists": participant.get("assists"),
                "gold_earned": participant.get("goldEarned"),
                "total_damage_dealt": participant.get("totalDamageDealtToChampions"),
                "vision_score": participant.get("visionScore"),
                "total_minions_killed": participant.get("totalMinionsKilled"),
                "damage_self_mitigated": participant.get("damageSelfMitigated"),
                "time_ccing_others": participant.get("timeCCingOthers"),
                "largest_killing_spree": participant.get("largestKillingSpree"),
                "largest_multi_kill": participant.get("largestMultiKill"),
                "win": participant.get("win")
            })
        return processed_data
    except Exception as e:
        print(f"Wystąpił błąd podczas przetwarzania danych meczu: {e}")
        return []

# Funkcja: Zapisz dane do pliku CSV
def save_to_csv(data, filename="all_league_data.csv"):
    if not data:
        print("Brak danych do zapisania.")
        return

    keys = data[0].keys()
    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows(data)
    print(f"Dane zapisano do pliku {filename}")

# Główna logika programu
if __name__ == "__main__":
    initial_puuid = "y9C_DhIv5uEzpvUL8HE95aL2OtIgh9gI25v0TS_aaFC8DQYXrWSr7r6HIEHsTxLvpxlL1VdeWkvOFQ"
    puuid_pool = {initial_puuid}
    processed_matches = set()
    all_data = []

    while len(all_data) < 200000:  # Limit danych do 100,000 wierszy
        if not puuid_pool:
            break

        current_puuid = puuid_pool.pop()
        print(f"Pobieranie historii meczów dla PUUID: {current_puuid}")
        match_history = get_match_history(current_puuid, count=10)

        for match_id in match_history:
            if match_id in processed_matches:
                continue
            print(f"Pobieranie szczegółów meczu {match_id}")
            match_data = get_match_details(match_id)
            if match_data:
                processed_matches.add(match_id)
                match_results = process_match_data(match_data, puuid_pool)
                all_data.extend(match_results)

                # Zapisuj co 10,000 wierszy, aby uniknąć utraty danych
                if len(all_data) % 10000 == 0:
                    save_to_csv(all_data, filename=f"league_data_{len(all_data)}.csv")
                    print(f"Zapisano dane po {len(all_data)} wierszach.")

                time.sleep(1)  # Wstrzymaj na 1 sekundę, aby uniknąć limitów API

    # Zapisz końcowy plik CSV
    if all_data:
        save_to_csv(all_data, filename="final_league_data.csv")
        print(f"Zapisano końcowe dane ({len(all_data)} wierszy).")
    else:
        print("Nie udało się zebrać wystarczających danych.")
