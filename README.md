# MPC → Ableton Drum Rack converter

Konwertuje programy perkusyjne MPC (`.xpm` typu Drum) na **drum racki Ableton Live 12** (`.adg`), zachowując fizyczny układ padów 1:1 z MPC. Wbudowany **edytor padów z odsłuchem** pozwala zagrać sample jak na MPC zanim wygenerujesz `.adg`.

Wszystkie sample są kopiowane obok pliku `.adg` — dostajesz samowystarczalny folder kitu, który można przenieść na inny komputer albo wrzucić do User Library.

## Funkcje

- **Konwersja `.xpm → .adg`** w trybie 1:1 (pad MPC nr N → drum rack pad nr N)
- **Edytor układu padów** z odsłuchem na żywo (jak beat machine):
  - 8 banków MPC (A-H), nawigacja po widoku drum racka
  - Klawiatura w stylu MPC: `1234` / `qwer` / `asdf` / `zxcv` = pady 13-16/9-12/5-8/1-4 bieżącego banku
  - Polifonia + niska latencja (pygame.mixer, ~6 ms)
  - **Mute / choke groups** — pady w tej samej grupie ucinają się wzajemnie (jak na MPC)
  - Kolorowanie padów po MuteGroup (na pierwszy rzut oka widać grupowanie)
  - Edycja MuteGroup w UI (prawy klik / Ctrl+klik) — zapisywane do `.adg`
  - Flash przy graniu, podświetlenie zaznaczonego pada
- **Podział wielobankowych kitów** na osobne `.adg` (np. kit z 4 bankami → 4 racki, każdy startuje od C1=36)
- **Przeniesienie MuteGroup z MPC** do ChokeGroup w Ableton drum racku
- Auto-uzupełnianie folderu sampli (folder XPM = domyślny folder ze źródłami)
- Drag & drop plików `.xpm` (z `tkinterdnd2`)

## Wymagania

- Python 3.10+
- macOS (skrypt używa `pygame` do audio, działa też na Linux/Windows)
- `pygame` — instalacja:
  ```
  pip3 install pygame
  ```
- Opcjonalnie: `tkinterdnd2` (drag-and-drop):
  ```
  pip3 install tkinterdnd2
  ```

Na macOS z Homebrew Python:
```
brew install python-tk@3.11   # tkinter dla Pythona 3.11 z brew
pip3 install pygame tkinterdnd2
```

## Uruchomienie

### GUI

```
python3 mpc2drumrack.py
```

1. Przeciągnij/wybierz pliki `.xpm` (folder ze samplami auto-uzupełni się)
2. (Opcjonalnie) wybierz inny folder ze samplami WAV (`.wav` MPC, rekursywnie)
3. Wybierz folder wyjściowy
4. (Opcjonalnie) otwórz **Pad Layout Editor** — przesuń pady, ustaw mute groups, odsłuchaj
5. (Opcjonalnie) zaznacz **„Podziel banki MPC na osobne drum racki"**
6. Kliknij **„Konwertuj"**

### CLI

Pojedynczy plik:
```
python3 mpc2drumrack.py kit.Drum.xpm /folder/sampli/ /folder/wyjsciowy/
```

Batch (rekursywnie wszystkie `.xpm` w folderze):
```
python3 mpc2drumrack.py --batch /folder/xpm/ /folder/sampli/ /folder/wyjsciowy/
```

Ręczne ustawienie pierwszej nuty (domyślnie 36 = C1):
```
python3 mpc2drumrack.py --first-note 24 kit.xpm samples/ out/
```

## Edytor układu padów

Otwiera się przyciskiem **„Otwórz Pad Layout Editor..."**. Działa jak beat machine:

| Akcja                           | Efekt                                        |
|---------------------------------|----------------------------------------------|
| Klik / klawisz na padzie MPC    | Zaznacza + odgrywa sample                    |
| Klik na drum rack pad           | Przypisuje zaznaczony sample / preview       |
| Podwójny klik na drum rack pad  | Usuwa przypisanie                            |
| Prawy klik / Ctrl+klik na padzie| Edytuj MuteGroup (0-16)                      |
| Klawisze `1234/qwer/asdf/zxcv`  | Pady 13-16 / 9-12 / 5-8 / 1-4 bieżącego banku |
| Radio Bank `A-H`                | Przełączanie między bankami MPC              |
| Strzałki widoku w drum racku    | Scroll widocznego okna 4×4                   |

Sample są preloadowane do RAM, polifonia 32 głosów. Mute groups dają choke MPC-style: nowy pad ucina poprzedni (włącznie z tym samym padem).

## Wynik konwersji

Z `POLISH SITAR.Drum.xpm` + folder ze samplami:

```
<output>/POLISH SITAR/
    POLISH SITAR.adg
    SITAR_01_POLISH.WAV
    KICK_01_SITAR_POLISH.WAV
    ...
```

`.adg` używa absolutnych ścieżek do sampli + nazwa pliku jako fallback. Live je znajdzie po dwukliku.

### Tryb „podziel banki"

Dla kitu z wieloma bankami (>16 instrumentów) z włączoną opcją **„Podziel banki MPC na osobne drum racki"**:

```
<output>/MyKit_A/MyKit_A.adg     ← pady 1-16 z MPC, wszystkie na C1-D#2
        /MyKit_A/<sample>.WAV
<output>/MyKit_B/MyKit_B.adg     ← pady 17-32 (lokalnie 1-16), też na C1-D#2
        /MyKit_B/<sample>.WAV
...
```

Każdy bank startuje od tej samej nuty (np. C1=36), pady numerowane lokalnie 1-16.

## Mapowanie 1:1 (tryb domyślny)

- MPC inst 1 (Bank A pad 1, lewy-dolny) → drum rack pad MIDI 36 (C1, lewy-dolny)
- MPC inst 16 (Bank A pad 16, prawy-górny) → drum rack MIDI 51 (D#2, prawy-górny)
- MPC inst 17 (Bank B pad 1) → drum rack MIDI 52 (E2, kontynuacja)
- ...

Puste pady na MPC = puste sloty w drum racku.

Skrypt bierze **tylko pierwszą niepustą warstwę** każdego pada MPC (zwykle Layer 1).

### Auto-mapowanie nut MIDI

Pierwsza nuta dobiera się automatycznie:

| Najwyższy używany pad | Pierwsza MIDI | Bank A widoczny od |
|-----------------------|---------------|--------------------|
| ≤ 92                  | 36 (C1)       | C1 (default)       |
| 92-128                | obniżona      | poniżej C1         |

Można wymusić: GUI „Auto-mapowanie nut" → wyłącz + spinbox „Pierwsza nuta", albo CLI `--first-note N`.

### MuteGroup → ChokeGroup

`MuteGroup` z każdego instrumentu MPC (1-16) trafia do `ChokeGroup` chainu drum racka. Pady w tej samej grupie ucinają się wzajemnie. W edytorze można też nadpisać MG ręcznie (prawy klik na padzie).

## Brakujące sample

Jeśli sample z `.xpm` nie zostanie znaleziony w folderze źródłowym (case-insensitive search rekursywny), GUI wypisuje listę i zostawia te pady puste.

## Quirk Ableton Live 12

Live 12 **ignoruje** wartości `<ReceivingNote>` w drum rack chains przy ładowaniu `.adg`. Zamiast tego mapuje pozycje branchy w pliku do MIDI na podstawie `<PadScrollPosition>` (`PSP*4` = MIDI najniższego widocznego pada). Skrypt to obchodzi:

- `PadScrollPosition = first_note // 4` (np. 9 dla MIDI 36)
- Branche w pliku w kolejności rosnącej po MIDI (Branch 0 = sample pod najniższą nutą)
- `<ReceivingNote>` ustawione na factory range 92→77 (Live i tak ignoruje)

Bez tego Ableton remapowałby twój kit do 77-92 (czyli F4-G#5) niezależnie od tego co napisane w pliku. (Patrz: [memory/live12_drum_rack_quirk.md](.claude/projects/.../live12_drum_rack_quirk.md))

## Standalone macOS app

```
pip3 install pyinstaller pygame tkinterdnd2
./build_app.sh
```

Wynik: `dist/MPC2DrumRack.app` (~63 MB, samodzielna, nie wymaga Pythona).
Przenieś do `/Applications` lub uruchom z miejsca:

```
open dist/MPC2DrumRack.app
```

## Struktura kodu

```
mpc2drumrack/
├─ mpc2drumrack.py       # skrypt (GUI + CLI + edytor)
├─ templates/
│  ├─ adg-head.xml       # nagłówek drum racka (Live 12 schema)
│  ├─ adg-tail.xml       # zamknięcie pliku
│  └─ pad.xml.tmpl       # szablon pojedynczego chainu z Simplerem
└─ README.md
```

Szablony pochodzą z drum racka zapisanego w Live 12.3 — modyfikacja schemy może zepsuć kompatybilność, ale kit ostatecznie ładuje się też w Live 11.

## Licencja

MIT.
