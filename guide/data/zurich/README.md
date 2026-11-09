# База мест: Цюрих (пилотный город)

Собрано 2026-11-09. Всё воспроизводимо скриптами из `tools/collect/`. Итог: `places.ndjson` (12 861 место, 12,7 МБ).

## 1. Источники

| Источник | Что | URL | Лицензия и требования | Записей (сырых) | Дата |
|---|---|---|---|---|---|
| OpenStreetMap (экстракт BBBike) | POI: еда, бары, магазины, музеи, спорт, парки и т.д. | https://download.bbbike.org/osm/bbbike/Zuerich/Zuerich.osm.pbf | ODbL 1.0; указывать «© OpenStreetMap contributors», производная база тоже под ODbL (share-alike) | 24 319 объектов (10 846 с именем) | 2026-11-09 (экстракт от 2026-11-07) |
| Zürich Tourismus (API zuerich.com) | кураторские места, описания de/en/fr/it, фото, часы | https://www.zuerich.com/en/api/v2/data | лицензия **на каждой записи**: CC BY-SA 1277, «только для организаций/редакционное продвижение туризма Цюриха» 139 (в данных `restricted-zurich-tourism`), CC BY-NC-ND 5, CC BY-NC 1; автор: «Zürich Tourismus, www.zuerich.com» | 1 422 (163 категории) | 2026-11-09 |
| Stadt Zürich Open Data (WFS) | спорт, парки, площадки, реестр заведений, искусство в городе, синие таблички, прогулки «Zürich z'Fuess», хорошие здания, кладбища, границы Kreis/Quartier | https://data.stadt-zuerich.ch/ | CC0 (CKAN `cc-zero` для всех наборов); атрибуция желательна, не обязательна | 10 944 объекта (18 наборов) | 2026-11-09 |
| Wikidata (SPARQL) | типы, подписи de/en/ru/fr/it, P18, sitelinks, сайт, архитектор, охрана | https://query.wikidata.org | CC0 | 5 279 объектов (7 450 с координатами в bbox/Q72; детали только для sitelinks≥1 или P131 Q72) | 2026-11-09 |
| Wikivoyage en + de | листинги see/do/eat/drink/buy/sleep + проза | https://en.wikivoyage.org/wiki/Zurich, https://de.wikivoyage.org/wiki/Zürich | CC BY-SA 4.0; ссылка на статью и лицензию | 299 листингов (en 166+24, de 104+5); районных подстатей нет | 2026-11-09 |
| Wikipedia en/de (extracts) | введение для 150 самых известных мест | https://{en,de}.wikipedia.org/w/api.php | CC BY-SA 4.0 | 270 отрывков | 2026-11-09 |
| Веб-поиск (5 запросов) | спорт для зрителей, купальни, сезонные события | см. `raw/websearch/websearch.json` | свои формулировки, вторичные источники | 10 заметок | 2026-11-09 |

Сводка по источникам: `sources.json`. Контекст для ИИ: `context/wikivoyage_*.md` (проза Wikivoyage: Understand, Get in/around, Stay safe и т.д., листинги сведены к названиям).

## 2. Схема `places.ndjson` (одна JSON-строка = одно место)

Пустые поля опускаются.

- `id` — стабильный: `wd:Q…` (есть Wikidata) → `osm:n|w|r<id>` → `zt:<id Tourism>` → `wv:<hash>` → `szh:<id>`. Если состав кластера изменится, id может смениться на другой тип.
- `name` (локальное по умолчанию), `names{de,en,fr,it,ru}`, `lat`, `lon` (приоритет: OSM > Tourism > Stadt ZH > Wikidata > Wikivoyage), `geom` = `area`, если точка — центроид полигона.
- `tier`: `core` (достойно показа в гиде) или `minor` (фон: мелкие объекты, реестр заведений, искусство, таблички и т.п.). Правило: core, если хоть один источник так считает: Tourism, Wikivoyage, Wikidata с sitelinks ≥3, OSM-еда/кафе/бары/музеи/галереи/достопримечательности/отели/театры/кино/парки/стадионы/спортцентры, Stadt ZH бассейны/катки/стадион.
- `group` — основная группа; `categories[]` — все `группа/подгруппа`, первая основная (слабые типы вроде `sights/attraction` уступают конкретным). Группы: sights, museums_art, nature_views, sports_active, sports_watch, food, cafes, bars_nightlife, shopping, culture_events, family, wellness, lodging, practical. Подгруппы описаны в `TAXONOMY` в `build_places.py`.
- `source_categories{источник:[…]}` — исходные категории (только у core).
- `descriptions[]`: `{lang, kind: short|long, text, source, url?}`; лицензия описания — в `sources[]` по полю `source`. Tourism: короткое ≤300 и длинное ≤500 символов (en, de), Wikipedia — первый абзац ≤700.
- `address{street,housenumber,postcode,city}`, `district{kreis, quartier}` (по полигонам Stadt ZH; вне города отсутствует).
- `opening_hours[]`: `{value, format: osm|schema_org|text|text_closing_hours_de, source}` — несколько вариантов, не сливаются.
- `website, phone, email, cuisine[], price` (текст из Tourism), `accessibility{wheelchair,source}` (из OSM), `attributes{}` (outdoor_seating, takeaway, diet:*, brand, stars, amenities Tourism, код доступности Stadt ZH и др.).
- `images[]`: `{url, license, author, source, caption?, page?}`. Фото Tourism: лицензия — записи, права на изображение отдельно не заявлены (проверять перед показом). Wikimedia: `license = see Commons file page`.
- `wikidata`, `wikidata_also[]` (слитые дубли-элементы), `wikipedia{enwiki,dewiki,…}`, `wikidata_facts{inception, architects, heritage, elevation_m, …}`.
- `popularity{wikidata_sitelinks, in_zuerich_tourism, in_wikivoyage[], n_sources, score}`; score = sitelinks + 6·Tourism + 5·Wikivoyage + 2·(n_sources−1) — грубая эвристика.
- `mentions[]` — заметки веб-поиска (пока 6), `{text, source_url, date}`.
- `sources[]`: `{source, id, url?, license, last_edit|modified?}`; `licenses[]` — сводка. У Tourism/Stadt ZH URL конкретного объекта нет (API/набор данных), см. `sources.json`.

## 3. Что исключено (правила)

Считано при сборке (`merge_report.json → dropped`): OSM — без имени 6 010, нет релевантного тега 5 808, информационные щиты 1 996, бассейны (`swimming_pool`, в основном частные) 1 540, закрытый доступ 548 (скамейки, питьевые фонтанчики, пикник-столы, настольный теннис, кострища не берутся вовсе). Stadt ZH — фонтаны 1 286, собачьи зоны 728, станции PubliBike 294, WC 139, пикник-места, линии (тропы), заведения типов Kleinverkaufsstelle/Nebenwirtschaft/Kiosk/Kantine/Tankstelle/Patentbefreit/Ausgabestelle (≈1 340). Wikidata — тип не релевантен 3 849, нет подписи 121, нет sitelinks 423. Wikivoyage — транспорт/школы/консульства (21).

## 4. Слияние и его качество

Шаги: (1) жёсткие связи — общий QID (321), `osm_id` из Tourism (508; ещё 136 не найдены в экстракте, 6 отклонены по расстоянию >400 м), тег OSM `wikipedia` (4); (2) листинги Wikivoyage без координат — только по единственному точному совпадению имени (15 размещено, **56 отброшено**); (3) нечёткое слияние между *разными* источниками: нормализованное имя (ё/ü→u, служебные слова, цифры должны совпадать, вложение токенов только если лишние слова — «Hotel», «Zürich», «Bar»…) + расстояние ≤120 м (≤300 м для площадных/Wikidata, ≤40 м для сетей и при sim<0.9); принято 2 357 пар (sim ≥0.98: 2 207; 0.9–0.98: 116; 0.8–0.9: 34); заблокировано из-за разных QID 87 и пересечения источников 1 055; (4) «строгий» проход: полностью совпадающее имя ≤80 м (≤250 м для площадей), совместимая группа — сливается даже при разных QID (отдельные элементы Wikidata для музея/здания/организации) или нескольких записях одного источника: 1 305 слияний, из них 22 с конфликтом QID, 1 207 внутри одного источника (в основном многослойные наборы Stadt ZH и повторные записи Tourism).

17 409 кандидатов → 12 861 место (слито 4 548). Мест с 1/2/3/4/5 источниками: 10 464 / 1 823 / 436 / 115 / 23.

Оценка качества (измерено, но косвенно — эталона нет): нечёткое правило нашло бы лишь **73 %** пар, связанных жёсткими ID (598 из 815), т.е. по этому образцу оставшихся дублей «в хвосте» может быть ~четверть; точность не измерена на размеченной выборке, только визуально по 25 случайным парам (ошибок не замечено, но ранее найденная проблема вложения имён, например «Glasfenster … im Grossmünster» ↔ «Grossmünster», была исправлена). Остаточные дубли: пар одного имени ≤60 м осталось 205, из них 19 core–core (`stats.json`), например Uetliberg, Rote Fabrik (площадка/детская площадка), Seebad Enge (два OSM-объекта с разными типами).

## 5. Покрытие (измерено, `stats.json`)

Места по группам (всего / core): sights 3 687/236, food 2 762/2 071, shopping 2 324/182, sports_active 967/276, cafes 593/507, bars_nightlife 493/397, practical 434/69, nature_views 400/175, culture_events 378/142, lodging 290/239, family 289/32, museums_art 206/192, wellness 29/29, sports_watch 9/9. Tier: core 4 556, minor 8 305.

Доля core-мест с полем: часы работы 62 %, любое описание 32 % (длинное 27 %), фото 28 %, сайт 69 %, телефон 43 %, адрес с улицей 73 %, район 80 %, Wikidata 10 %, кухня 37 %, цена 3 %, доступность 49 %, название на en 31 %. Для всех мест: часы 40 %, описание 38 %, фото 13 %, сайт 41 %.
Слабые места: sights core — часы 15 %; sports_active core — описание 21 %; кафе — описание 11 %, фото 9 %.

## 6. Известные проблемы и пробелы

- **События и сезонные вещи** (Street Parade, Sechseläuten, рождественские рынки) — не места; в базе отсутствуют, есть только заметки в `raw/websearch/websearch.json` (даты 2026 не подтверждены).
- **Спорт для зрителей**: только 9 мест (Letzigrund, Hallenstadion, Swiss Life Arena и др.), нет привязки клубов (FCZ, GC, ZSC Lions) к местам и расписания.
- **Популярность «сейчас»** (модные бары, рестораны): открытые данные её не дают; веб-поиск вернул мало надёжного (нужен отдельный источник; коммерческие рейтинги не копировались).
- Категории сильно зависят от источника: OSM-теги / типы schema.org Tourism; есть ошибки (например OSM «Zürich Landesmuseum» как theatre, Fernsehturm как bike_hike у Tourism). Подгруппы — советующие.
- `food/unclassified` (1 575): реестр заведений Stadt ZH без типа кухни; тип угадывается по ключевым словам в названии, остальное — просто «заведение».
- Tourism: 139 записей с ограниченной лицензией (используются в рекомендациях/редакционно для продвижения туризма Цюриха, не для общего использования); 5 CC BY-NC-ND — запрещены производные изменения, 1 CC BY-NC. Фото: права на изображения отдельно не указаны.
- Wikivoyage de: 56 листингов без координат отброшены (нет однозначного совпадения). На en нет подстатей по районам.
- Wikidata: `no sitelinks` и нетипизированные объекты исключены; около 4 000 элементов не вошло (дома, остановки и т.д.); ru-метки есть не везде (name_en у core только 31 %, ru ещё реже).
- Часы работы в нескольких форматах (`osm`, `schema_org`, `text`, `text_closing_hours_de`) — парсера в единый вид нет; у реестра Stadt ZH указано только время закрытия.
- Адреса Stadt ZH/Tourism не геокодировались; координаты берутся из источников (смещения до ~50 м возможны).
- Размер: `guide/` без кэша ≈ 42 МБ (чуть выше бюджета 40; места 12,7 МБ, сырые Tourism 10,5 МБ, Stadt ZH 8,4 МБ, OSM 6,7 МБ, Wikidata 3,3 МБ).
- Заблокированные хосты не встречались; Wikipedia API отвечал 429, но retry-after соблюдался, итог 270 отрывков из 270 запрошенных.

## 7. Как перезапустить

```
cd tools/collect
python3 fetch_osm.py                # нужен osmium; кэш PBF в data/zurich/cache
python3 fetch_zuerich_tourism.py
python3 fetch_stadt_zuerich.py
python3 fetch_wikidata.py           # ~15 мин, WDQS иногда отвечает 429/504 (есть повторы)
python3 fetch_wikivoyage.py         # ~1 мин, пауза 4 с между запросами
python3 build_places.py             # → places.ndjson, merge_report.json, sources.json
python3 fetch_wikipedia.py          # читает places.ndjson; затем снова build_places.py
python3 check_places.py             # → stats.json, проверки, выборочные точки
```
Определения групп/правила — в `build_places.py` (`OSM_TABLE`, `TOURISM_TYPE`, `SZH_LAYER`, `WD_TYPES`, `GASTRO_KEEP`). Все скрипты используют общий `common.py`/`config.py`; другой город добавляется в `config.py`.
