#!/usr/bin/env python3
"""
MPC -> Ableton Drum Rack converter (układ 1:1).

Bierze plik .xpm (MPC drum program) + folder ze źródłowymi samplami,
i tworzy folder kitu zawierający:
    <output>/<NazwaProgramu>/<NazwaProgramu>.adg
    <output>/<NazwaProgramu>/<sample_1>.wav
    <output>/<NazwaProgramu>/<sample_2>.wav
    ...

Każdy MPC instrument N (= fizyczny pad N na MPC) trafia na drum rack pad
na pozycji N. Pady puste w MPC = puste w drum racku. Bierze tylko pierwszą
niepustą warstwę (Layer) z każdego pada.

Mapowanie nut auto-dopasowane: jeśli najwyższy używany instrument <= 92,
pierwszy pad = MIDI 36 (C1, Ableton default). Jeśli wyżej, pierwsza nuta
jest obniżana, by zmieścić wszystko w 0..127.

Uruchomienie:
    GUI:    python3 mpc2drumrack.py
    CLI:    python3 mpc2drumrack.py <input.xpm> <samples_src> <output_dir>
    Batch:  python3 mpc2drumrack.py --batch <folder_xpm> <samples_src> <output_dir>

Bazuje na konwencji XML z pkMinhas/DrumKitCreator (MIT).
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from html import escape as html_escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Konstanty
# ---------------------------------------------------------------------------

# W PyInstaller (frozen) zasoby są w sys._MEIPASS; przy uruchomieniu z .py
# obok skryptu.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    TEMPLATES_DIR = Path(sys._MEIPASS) / "templates"
else:
    TEMPLATES_DIR = Path(__file__).parent / "templates"

SAMPLE_EXTENSIONS = (".wav", ".WAV", ".aif", ".aiff", ".AIF", ".AIFF",
                     ".flac", ".FLAC", ".mp3", ".MP3", ".ogg", ".OGG")

DEFAULT_FIRST_NOTE = 36  # C1 - pierwszy widoczny pad w drum racku
MAX_NOTE = 127


# ---------------------------------------------------------------------------
# Wczytanie szablonów XML
# ---------------------------------------------------------------------------


def load_templates() -> Tuple[str, str, str]:
    head = (TEMPLATES_DIR / "adg-head.xml").read_text(encoding="utf-8")
    tail = (TEMPLATES_DIR / "adg-tail.xml").read_text(encoding="utf-8")
    pad_tmpl = (TEMPLATES_DIR / "pad.xml.tmpl").read_text(encoding="utf-8")
    return head, tail, pad_tmpl


# ---------------------------------------------------------------------------
# Parsowanie .xpm
# ---------------------------------------------------------------------------


def parse_xpm(xpm_path: Path) -> Tuple[str, Dict[int, str], Dict[int, int]]:
    """
    Parsuje plik .xpm (MPC drum program).

    Zwraca (program_name, {instrument_number: sample_name},
            {instrument_number: mute_group}).
    Bierze tylko pierwszą niepustą warstwę z każdego instrumentu.
    Numerowanie 1-based (zgodnie z atrybutem MPC).
    MuteGroup z MPC mapuje się 1:1 na ChokeGroup w drum racku Live'a.
    """
    tree = ET.parse(xpm_path)
    root = tree.getroot()

    program = next(
        (p for p in root.findall("Program") if p.get("type") == "Drum"),
        None,
    )
    if program is None:
        raise ValueError(f"Nie znaleziono programu typu Drum w {xpm_path}")

    name_node = program.find("ProgramName")
    program_name = (name_node.text.strip() if name_node is not None and name_node.text
                    else xpm_path.stem)

    pad_map: Dict[int, str] = {}
    mute_map: Dict[int, int] = {}
    instruments_node = program.find("Instruments")
    if instruments_node is None:
        return program_name, pad_map, mute_map

    for instrument in instruments_node.findall("Instrument"):
        try:
            num = int(instrument.get("number") or 0)
        except ValueError:
            continue
        if num <= 0:
            continue

        layers_node = instrument.find("Layers")
        if layers_node is None:
            continue

        for layer in layers_node.findall("Layer"):
            sn = layer.find("SampleName")
            if sn is not None and sn.text:
                name = sn.text.strip()
                if name:
                    pad_map[num] = name
                    break  # tylko pierwsza niepusta warstwa

        mg = instrument.find("MuteGroup")
        if mg is not None and mg.text:
            try:
                mute_map[num] = int(mg.text.strip())
            except ValueError:
                pass

    return program_name, pad_map, mute_map


# ---------------------------------------------------------------------------
# Wyszukiwanie sampli
# ---------------------------------------------------------------------------


def build_sample_index(samples_dir: Path) -> Dict[str, Path]:
    """Indeksuje folder rekursywnie. Klucz = nazwa bez rozszerzenia (lower)."""
    index: Dict[str, Path] = {}
    for root, _dirs, files in os.walk(samples_dir):
        for f in files:
            base, ext = os.path.splitext(f)
            if ext in SAMPLE_EXTENSIONS:
                key = base.lower()
                if key not in index:
                    index[key] = Path(root) / f
                else:
                    cur_ext = index[key].suffix.lower()
                    if cur_ext != ".wav" and ext.lower() == ".wav":
                        index[key] = Path(root) / f
    return index


def resolve_sample(name: str, sample_index: Dict[str, Path]) -> Optional[Path]:
    return sample_index.get(name.lower())


# ---------------------------------------------------------------------------
# Generowanie XML drum racka
# ---------------------------------------------------------------------------


def make_path_hint_xml(sample_path: Path) -> str:
    """Generuje fragment <RelativePathElement Id="..." Dir="..."> dla każdego
    katalogu w ścieżce (oprócz roota i nazwy pliku)."""
    parts = sample_path.parts
    if len(parts) <= 2:
        return ""
    middle = parts[1:-1]
    fragments = [
        f'<RelativePathElement Id="{i + 8}" Dir="{html_escape(d)}" />'
        for i, d in enumerate(middle)
    ]
    return "\n".join(fragments)


def render_pad(pad_tmpl: str, pad_id: int, pad_note: int,
               sample_path: Path, choke_group: int = 0) -> str:
    """Wypełnia szablon pojedynczego pada."""
    sample_filename = sample_path.name
    sample_name_no_ext = sample_path.stem
    path_hint = make_path_hint_xml(sample_path)

    try:
        file_size = sample_path.stat().st_size
    except OSError:
        file_size = 0

    abs_path = str(sample_path.resolve())

    out = pad_tmpl
    out = out.replace("<%- PadNumber %>", str(pad_id))
    out = out.replace("<%- SampleNameWithoutWav %>", html_escape(sample_name_no_ext))
    out = out.replace("<%- SampleName %>", html_escape(sample_filename))
    out = out.replace("<%= PadNote %>", str(pad_note))
    out = out.replace("<%= ChokeGroup %>", str(choke_group))
    out = out.replace("__SAMPLE_ABS_PATH__", html_escape(abs_path))

    out = re.sub(
        r"<PathHint>.*?</PathHint>",
        f"<PathHint>\n{path_hint}\n</PathHint>",
        out, count=1, flags=re.DOTALL,
    )
    out = re.sub(r'<FileSize Value="\d+" />',
                 f'<FileSize Value="{file_size}" />', out, count=1)
    out = re.sub(r'<Crc Value="\d+" />', '<Crc Value="0" />', out, count=1)
    out = re.sub(r'<HasExtendedInfo Value="true" />',
                 '<HasExtendedInfo Value="false" />', out, count=1)
    return out


def auto_first_note(max_instrument: int,
                    preferred: int = DEFAULT_FIRST_NOTE) -> int:
    """
    Wybiera pierwszą nutę MIDI tak, by wszystkie pady się zmieściły.
    Idealnie 36 (Ableton default). Jeśli max_instrument przekracza 92,
    obniżamy pierwszą nutę.
    """
    max_first = MAX_NOTE - max_instrument + 1  # max_first + max_instrument - 1 = 127
    if max_first < 0:
        return 0  # i tak nie zmieści się wszystko, ale start od 0 daje max
    return min(preferred, max_first)


def build_adg_xml(pad_map: Dict[int, Path], head: str, tail: str,
                  pad_tmpl: str,
                  first_note: Optional[int] = None,
                  midi_map: Optional[Dict[int, int]] = None,
                  mute_map: Optional[Dict[int, int]] = None
                  ) -> Tuple[str, int]:
    """
    Składa XML drum racka.

    pad_map: {instrument_number(1-based): sciezka_do_skopiowanego_sampla}
    Jedna z dwóch opcji mapowania:
      - first_note: nuta MIDI, na którą trafia instrument nr 1 (1:1 ascending)
      - midi_map: {midi_note: instrument_number} - własne mapowanie

    Zwraca (xml_str, liczba_truncated_padow).
    """
    pads_xml: List[str] = []
    truncated = 0
    branch_id = 0

    # WAŻNE: Ableton Live ignoruje wartości <ReceivingNote> w plikach .adg dla
    # drum racków. Live mapuje Branch_index -> MIDI = PadScrollPosition*4 + index,
    # a samo ReceivingNote w pliku powinno mieć "factory default" zakres 77..92
    # (inaczej Live source-fixuje plik niepredictably). Faktyczna nuta MIDI
    # jest sterowana przez PadScrollPosition oraz kolejność branchy.
    if midi_map is not None:
        # Pary (target_midi, inst_num) - sortuj rosnąco po MIDI:
        # Branch 0 = najniższa nuta (bottom-left), Branch N = wyższe.
        valid = [(m, midi_map[m]) for m in sorted(midi_map.keys())
                 if 0 <= m <= MAX_NOTE and midi_map[m] in pad_map]
        truncated = sum(1 for m, n in midi_map.items()
                        if not (0 <= m <= MAX_NOTE) and n in pad_map)
        first_visible = valid[0][0] if valid else DEFAULT_FIRST_NOTE
    else:
        fn = first_note if first_note is not None else DEFAULT_FIRST_NOTE
        valid = []
        for inst_num in sorted(pad_map.keys()):
            pad_note = fn + inst_num - 1
            if pad_note > MAX_NOTE or pad_note < 0:
                truncated += 1
                continue
            valid.append((pad_note, inst_num))
        first_visible = valid[0][0] if valid else DEFAULT_FIRST_NOTE

    # ReceivingNote w pliku: użyj factory default (77..92) niezależnie od
    # docelowej nuty - Live i tak czyta to jako "indeks z górnego rzędu".
    # Dla N chainów: Branch 0 = 92, Branch 1 = 91, ..., Branch N-1 = 92-(N-1).
    # Przy renderze Branch 0 = "najniższa pozycja" (bottom-left wizualnego 4x4).
    # Branche zapisujemy w pliku w kolejności: Branch_id 0 = sample dla
    # najniższej nuty, ... Branch_id 15 = sample dla najwyższej.
    # Konwencja v7 (działająca): Branch 0 ma <ReceivingNote>=92 i ląduje na
    # NAJNIŻSZEJ MIDI w widocznym 4x4 (PSP*4). Branch N-1 ma <ReceivingNote>=
    # 92-(N-1) i ląduje na najwyższej. Czyli wartość RecvNote w pliku to
    # malejąca seria 92,91,..., niezależna od docelowych MIDI.
    FACTORY_HIGHEST = 92
    for idx, (target_midi, inst_or_pad) in enumerate(valid):
        sample_path = pad_map[inst_or_pad]
        receiving_note = FACTORY_HIGHEST - idx
        choke = mute_map.get(inst_or_pad, 0) if mute_map else 0
        pads_xml.append(render_pad(pad_tmpl, idx, receiving_note,
                                   sample_path, choke_group=choke))

    pad_scroll = max(0, first_visible // 4)

    head_filled = head.replace("__PAD_SCROLL_POSITION__", str(pad_scroll))

    return head_filled + "\n" + "\n".join(pads_xml) + "\n" + tail, truncated


def write_adg(adg_path: Path, xml_str: str) -> None:
    data = xml_str.encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(data)
    adg_path.write_bytes(buf.getvalue())


# ---------------------------------------------------------------------------
# Konwersja
# ---------------------------------------------------------------------------


class ConversionResult:
    def __init__(self, xpm: Path, kit_dir: Path, adg: Path,
                 placed: int, missing: List[str], truncated: int,
                 first_note: int):
        self.xpm = xpm
        self.kit_dir = kit_dir
        self.adg = adg
        self.placed = placed
        self.missing = missing
        self.truncated = truncated
        self.first_note = first_note


def safe_dirname(name: str) -> str:
    """Zamienia znaki nieakceptowane przez systemy plików."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return cleaned or "Untitled"


def convert_one(xpm_path: Path, samples_src_dir: Path, output_root: Path,
                first_note: Optional[int] = None,
                midi_map: Optional[Dict[int, int]] = None,
                mute_map: Optional[Dict[int, int]] = None,
                progress=None) -> ConversionResult:
    """Konwertuje 1 plik .xpm; tworzy folder kitu z .adg i kopią sampli.

    Jeśli podany midi_map: {midi_note: instrument_num}, używa go zamiast
    automatycznego mapowania first_note + inst - 1.
    Jeśli podany mute_map: {inst_num: mute_group_num}, używa go zamiast
    MuteGroup z .xpm.
    """
    if progress:
        progress(f"Parsuję {xpm_path.name}...")
    program_name, pad_map_names, pad_mute_groups = parse_xpm(xpm_path)
    if mute_map is not None:
        pad_mute_groups = mute_map
    if not pad_map_names:
        raise ValueError(f"{xpm_path.name}: brak instrumentów z samplami")

    if progress:
        progress(f"Indeksuję sample źródłowe w {samples_src_dir}...")
    sample_index = build_sample_index(samples_src_dir)

    # Resolwuj sample i przygotuj folder docelowy
    kit_dir = output_root / safe_dirname(program_name)
    kit_dir.mkdir(parents=True, exist_ok=True)

    pad_map_paths: Dict[int, Path] = {}
    missing: List[str] = []
    copied_files: Dict[str, Path] = {}  # cache: nazwa_lower -> sciezka_w_kicie

    for inst_num, sample_name in pad_map_names.items():
        src = resolve_sample(sample_name, sample_index)
        if src is None:
            missing.append(sample_name)
            continue

        # Skopiuj do folderu kitu (jeśli jeszcze nie skopiowano)
        key = src.name.lower()
        if key in copied_files:
            dst = copied_files[key]
        else:
            dst = kit_dir / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
            copied_files[key] = dst

        pad_map_paths[inst_num] = dst

    if not pad_map_paths:
        raise FileNotFoundError(
            f"Żaden sample z {xpm_path.name} nie został znaleziony w "
            f"{samples_src_dir}.\nBrakujące: {missing[:10]}"
            f"{' (...)' if len(missing) > 10 else ''}"
        )

    # Auto-dopasowanie pierwszej nuty (jeśli nie używamy custom mapy)
    max_inst = max(pad_map_paths.keys())
    if midi_map is None:
        fn = first_note if first_note is not None else auto_first_note(max_inst)
    else:
        fn = first_note or DEFAULT_FIRST_NOTE  # placeholder, nie używane gdy midi_map ustawione

    if progress:
        progress(f"Skopiowano {len(copied_files)} unikalnych sampli do {kit_dir.name}/")
        if midi_map is not None:
            progress(f"Używam custom layout: {len(midi_map)} pozycji")
        else:
            progress(f"Mapowanie: instrument 1 -> MIDI {fn}, "
                     f"instrument {max_inst} -> MIDI {fn + max_inst - 1}")

    # Generuj XML
    head, tail, pad_tmpl = load_templates()
    if midi_map is not None:
        xml_str, truncated = build_adg_xml(pad_map_paths, head, tail, pad_tmpl,
                                            midi_map=midi_map,
                                            mute_map=pad_mute_groups)
    else:
        xml_str, truncated = build_adg_xml(pad_map_paths, head, tail, pad_tmpl,
                                            first_note=fn,
                                            mute_map=pad_mute_groups)
    if truncated and progress:
        progress(f"UWAGA: {truncated} pad(ów) poza zakresem MIDI 0-127 - pominięte.")

    adg_path = kit_dir / (safe_dirname(program_name) + ".adg")
    if progress:
        progress(f"Zapisuję {adg_path.name}...")
    write_adg(adg_path, xml_str)

    return ConversionResult(
        xpm=xpm_path, kit_dir=kit_dir, adg=adg_path,
        placed=len(pad_map_paths) - truncated,
        missing=missing, truncated=truncated, first_note=fn,
    )


def convert_split_banks(xpm_path: Path, samples_src_dir: Path, output_root: Path,
                         first_note: Optional[int] = None,
                         midi_map: Optional[Dict[int, int]] = None,
                         mute_map: Optional[Dict[int, int]] = None,
                         progress=None) -> List[ConversionResult]:
    """Konwertuje XPM dzieląc po bankach (16 padów = 1 bank = 1 .adg).
    Każdy bank dostaje pady ponumerowane lokalnie 1-16 i startuje od first_note.
    Zwraca listę ConversionResult (jeden na bank z samplami).
    """
    if progress:
        progress(f"Parsuję {xpm_path.name}...")
    program_name, pad_map_names, pad_mute_groups = parse_xpm(xpm_path)
    if mute_map is not None:
        pad_mute_groups = mute_map
    if not pad_map_names:
        raise ValueError(f"{xpm_path.name}: brak instrumentów z samplami")

    # Grupowanie po bankach (16 padów na bank)
    banks: Dict[int, Dict[int, str]] = {}
    bank_mutes: Dict[int, Dict[int, int]] = {}
    for inst_num, sample_name in pad_map_names.items():
        bank_idx = (inst_num - 1) // 16
        pad_local = (inst_num - 1) % 16 + 1
        banks.setdefault(bank_idx, {})[pad_local] = sample_name
        if inst_num in pad_mute_groups:
            bank_mutes.setdefault(bank_idx, {})[pad_local] = pad_mute_groups[inst_num]

    if len(banks) <= 1:
        # Tylko jeden bank - normalna konwersja
        return [convert_one(xpm_path, samples_src_dir, output_root,
                            first_note=first_note, midi_map=midi_map,
                            mute_map=mute_map, progress=progress)]

    if progress:
        progress(f"Indeksuję sample źródłowe w {samples_src_dir}...")
    sample_index = build_sample_index(samples_src_dir)

    fn_default = first_note if first_note is not None else DEFAULT_FIRST_NOTE
    head, tail, pad_tmpl = load_templates()
    results: List[ConversionResult] = []

    for bank_idx in sorted(banks.keys()):
        bank_letter = chr(ord("A") + bank_idx) if bank_idx < 8 else f"BANK{bank_idx + 1}"
        bank_name = f"{program_name}_{bank_letter}"
        kit_dir = output_root / safe_dirname(bank_name)
        kit_dir.mkdir(parents=True, exist_ok=True)

        # Resolve sampli i kopia do folderu kitu
        pad_map_paths: Dict[int, Path] = {}
        missing: List[str] = []
        copied: Dict[str, Path] = {}
        for pad_local, sample_name in banks[bank_idx].items():
            src = resolve_sample(sample_name, sample_index)
            if src is None:
                missing.append(sample_name)
                continue
            key = src.name.lower()
            if key in copied:
                dst = copied[key]
            else:
                dst = kit_dir / src.name
                if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                    shutil.copy2(src, dst)
                copied[key] = dst
            pad_map_paths[pad_local] = dst

        if not pad_map_paths:
            if progress:
                progress(f"  Bank {bank_letter}: brak sampli, pomijam")
            continue

        # midi_map zwykle nie jest używany w trybie split (custom layout
        # zapisany w edytorze odnosi się do globalnych inst_num).
        # Filtrujemy do bieżącego banku jeśli się da.
        bank_midi_map = None
        if midi_map is not None:
            local_map = {}
            for midi, inst_num in midi_map.items():
                if (inst_num - 1) // 16 == bank_idx:
                    local_map[midi] = (inst_num - 1) % 16 + 1
            if local_map:
                bank_midi_map = local_map

        if bank_midi_map is not None:
            xml_str, truncated = build_adg_xml(
                pad_map_paths, head, tail, pad_tmpl,
                midi_map=bank_midi_map,
                mute_map=bank_mutes.get(bank_idx),
            )
        else:
            xml_str, truncated = build_adg_xml(
                pad_map_paths, head, tail, pad_tmpl,
                first_note=fn_default,
                mute_map=bank_mutes.get(bank_idx),
            )

        adg_path = kit_dir / (safe_dirname(bank_name) + ".adg")
        write_adg(adg_path, xml_str)

        if progress:
            progress(f"  Bank {bank_letter} -> {kit_dir.name}/{adg_path.name} "
                     f"({len(pad_map_paths)} padów"
                     + (f", brak {len(missing)}" if missing else "") + ")")

        results.append(ConversionResult(
            xpm=xpm_path, kit_dir=kit_dir, adg=adg_path,
            placed=len(pad_map_paths) - truncated,
            missing=missing, truncated=truncated, first_note=fn_default,
        ))

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Konwerter MPC .xpm -> Ableton .adg (1:1)")
    p.add_argument("input", nargs="?", help="plik .xpm lub katalog (z --batch)")
    p.add_argument("samples_src", nargs="?", help="katalog ze źródłowymi samplami")
    p.add_argument("output_dir", nargs="?", help="katalog wyjściowy (gdzie powstanie folder kitu)")
    p.add_argument("--batch", action="store_true",
                   help="konwertuj wszystkie .xpm w katalogu rekursywnie")
    p.add_argument("--first-note", type=int, default=None,
                   help="MIDI nuta dla instrumentu nr 1 (domyślnie auto: 36 lub niżej)")
    args = p.parse_args(argv)

    if not args.input:
        return run_gui()

    if not (args.samples_src and args.output_dir):
        p.error("Podaj: <input> <samples_src> <output_dir>")

    inp = Path(args.input).expanduser()
    src = Path(args.samples_src).expanduser()
    out = Path(args.output_dir).expanduser()

    if not src.is_dir():
        p.error(f"Folder ze samplami nie istnieje: {src}")

    if args.batch:
        if not inp.is_dir():
            p.error("--batch wymaga katalogu jako input")
        ok, fail = 0, 0
        for xpm in sorted(inp.rglob("*.xpm")):
            try:
                res = convert_one(xpm, src, out, first_note=args.first_note,
                                  progress=lambda m: print(f"  {m}"))
                ok += 1
                print(f"[OK] {xpm.name} -> {res.kit_dir}/{res.adg.name} "
                      f"({res.placed} padów, {len(res.missing)} brak, {res.truncated} ucięte)")
            except Exception as e:
                fail += 1
                print(f"[FAIL] {xpm.name}: {e}", file=sys.stderr)
        print(f"\nGotowe: {ok} ok, {fail} błędów.")
        return 0 if fail == 0 else 1

    if not inp.is_file():
        p.error(f"Plik nie istnieje: {inp}")

    res = convert_one(inp, src, out, first_note=args.first_note,
                      progress=lambda m: print(m))
    print(f"\nKit gotowy: {res.kit_dir}")
    print(f"  .adg:    {res.adg.name}")
    print(f"  Pady:    {res.placed}")
    print(f"  Pierwsza nuta: MIDI {res.first_note}")
    if res.missing:
        print(f"\nUWAGA: {len(res.missing)} sampli nie znaleziono w {src}:")
        for s in res.missing[:20]:
            print(f"  - {s}")
        if len(res.missing) > 20:
            print(f"  ... oraz {len(res.missing) - 20} więcej")
    if res.truncated:
        print(f"\nUWAGA: {res.truncated} padów ucięto (poza MIDI 0-127). "
              f"Użyj --first-note 0 by zmieścić więcej.")
    return 0


# ---------------------------------------------------------------------------
# Pad Layout Editor (Toplevel window)
# ---------------------------------------------------------------------------


def open_layout_editor(parent_root, mpc_pad_map, on_save_callback,
                       initial_midi_map=None, samples_src_dir=None,
                       mute_map=None):
    """
    Otwiera edytor pad layoutu.

    mpc_pad_map: {instrument_num: sample_name} z .xpm
    on_save_callback: wywoływane z midi_map={midi: instrument_num} po zapisie
    initial_midi_map: opcjonalne początkowe mapowanie
    samples_src_dir: jeśli podany - preload sampli do pamięci, niska latencja
    mute_map: {inst_num: mute_group} - choke group, w tym samym MG > 0 grają
              ekskluzywnie (nowy ucinka poprzedni)
    """
    import tkinter as tk
    from tkinter import ttk

    # Lokalna kopia (edytowalna) - zwracana przez on_save_callback
    mute_map = dict(mute_map) if mute_map else {}
    status_msg = {"set": lambda *_: None}

    # ---- Audio: pygame.mixer (niska latencja, polifonia, choke groups) ----
    sounds = {}    # inst_num -> pygame.mixer.Sound
    channels = {}  # inst_num -> last Channel obj (do choke)
    audio_ready = False
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.pre_init(44100, -16, 2, 256)
            pygame.mixer.init()
        pygame.mixer.set_num_channels(32)
        audio_ready = True
    except Exception as e:
        status_msg["set"](f"Audio off: {e}")

    if audio_ready and samples_src_dir and Path(samples_src_dir).is_dir():
        sample_index = build_sample_index(Path(samples_src_dir))
        loaded = 0
        skipped = []
        for inst_num, name in mpc_pad_map.items():
            src = resolve_sample(name, sample_index)
            if src is None:
                skipped.append(name)
                continue
            try:
                sounds[inst_num] = pygame.mixer.Sound(str(src))
                loaded += 1
            except Exception:
                skipped.append(name)
        status_msg["set"](
            f"Załadowano {loaded} sampli"
            + (f", brak {len(skipped)}" if skipped else "")
        )

    def play_sample(inst_num):
        if not audio_ready:
            status_msg["set"](f"P{inst_num}: audio niedostępne")
            return
        snd = sounds.get(inst_num)
        if snd is None:
            name = mpc_pad_map.get(inst_num, "(pusty)")
            status_msg["set"](f"P{inst_num} '{name}': brak sampla")
            return
        # Choke / mute group: zatrzymaj WSZYSTKIE pady z tym samym MG > 0
        # (włącznie z tym samym padem - zachowanie MPC: re-trigger ucina poprzedni)
        mg = mute_map.get(inst_num, 0)
        if mg:
            for other_inst, ch in list(channels.items()):
                if ch is None:
                    continue
                if mute_map.get(other_inst, 0) != mg:
                    continue
                try:
                    if ch.get_busy():
                        ch.stop()
                except Exception:
                    pass
        # Polifonia (gdy MG=0) lub re-trigger (gdy MG>0): zagraj na nowym kanale
        try:
            ch = snd.play()
            channels[inst_num] = ch
            mg_label = f" [MG{mg}]" if mg else ""
            name = mpc_pad_map.get(inst_num, "")
            status_msg["set"](f"♪ P{inst_num}{mg_label}: {name}")
        except Exception as e:
            status_msg["set"](f"BŁĄD: {e}")

    win = tk.Toplevel(parent_root)
    win.title("Pad Layout Editor")
    win.geometry("980x720")

    # Stan
    if initial_midi_map:
        midi_map = dict(initial_midi_map)
    else:
        midi_map = {DEFAULT_FIRST_NOTE + n - 1: n
                    for n in sorted(mpc_pad_map.keys())
                    if DEFAULT_FIRST_NOTE + n - 1 <= MAX_NOTE}

    state = {
        "selected_inst": None,
        "mpc_bank": 0,    # 0=A, 1=B, ..., 7=H
        "dr_view_start": DEFAULT_FIRST_NOTE,  # bottom-left MIDI of visible 4x4
    }

    # ---- UI layout ----
    main = ttk.Frame(win)
    main.pack(fill="both", expand=True, padx=10, pady=10)

    # Paleta kolorów dla 16 mute groupów (pastelowe)
    MG_COLORS = [
        None,        # MG 0 = brak choke
        "#ffcdd2",   # 1 - różowy
        "#c5cae9",   # 2 - liliowy
        "#c8e6c9",   # 3 - mięta
        "#ffe0b2",   # 4 - brzoskwinia
        "#b2ebf2",   # 5 - cyjan
        "#f8bbd0",   # 6 - magenta jasna
        "#dcedc8",   # 7 - limonka
        "#d1c4e9",   # 8 - lawenda
        "#fff9c4",   # 9 - żółty
        "#b3e5fc",   # 10 - błękit
        "#ffccbc",   # 11 - łosoś
        "#bbdefb",   # 12 - niebieski
        "#cfd8dc",   # 13 - szary
        "#f0f4c3",   # 14 - oliwka
        "#e1bee7",   # 15 - fiolet
        "#b2dfdb",   # 16 - turkus
    ]

    def inst_color(inst, selected=False, assigned=False):
        if inst is None:
            return "#f0f0f0"
        if selected:
            return "#ffd54f"  # zaznaczony - żółty
        # Tinta po MuteGroup (jeśli ustawiona) - widoczne grupowanie
        mg = mute_map.get(inst, 0) if mute_map else 0
        if 0 < mg < len(MG_COLORS) and MG_COLORS[mg]:
            return MG_COLORS[mg]
        if assigned:
            return "#bbdefb"  # przypisany ale bez MG - jasnoniebieski
        return "#e0e0e0"

    # Hint label
    hint = ttk.Label(
        win,
        text="Klik = zaznacz+graj  (klawisze 1234/qwer/asdf/zxcv = pady 13-16/9-12/5-8/1-4)  •  Drum rack: klik = przypisz/preview, podwójny klik = usuń  •  Prawy klik (lub Ctrl+klik) na padzie = edytuj MuteGroup",
        foreground="#444",
        wraplength=940,
    )
    hint.pack(padx=10, pady=(0, 6))

    # ---- LEWA strona: MPC bank ----
    mpc_frame = ttk.LabelFrame(main, text="MPC Pads (banki A-H)")
    mpc_frame.pack(side="left", fill="both", expand=True, padx=4)

    mpc_top = ttk.Frame(mpc_frame)
    mpc_top.pack(fill="x", padx=6, pady=6)
    ttk.Label(mpc_top, text="Bank:").pack(side="left")

    bank_var = tk.IntVar(value=0)
    for i, letter in enumerate("ABCDEFGH"):
        rb = ttk.Radiobutton(mpc_top, text=letter, variable=bank_var, value=i,
                             command=lambda: refresh_all())
        rb.pack(side="left", padx=2)

    mpc_grid = ttk.Frame(mpc_frame)
    mpc_grid.pack(padx=10, pady=6)

    # ---- PRAWA strona: Drum Rack visible ----
    dr_frame = ttk.LabelFrame(main, text="Drum Rack (widok 4x4)")
    dr_frame.pack(side="left", fill="both", expand=True, padx=4)

    dr_top = ttk.Frame(dr_frame)
    dr_top.pack(fill="x", padx=6, pady=6)
    ttk.Label(dr_top, text="MIDI od (bottom-left):").pack(side="left")
    view_start_var = tk.IntVar(value=DEFAULT_FIRST_NOTE)

    def shift_view(delta):
        new_val = max(0, min(112, view_start_var.get() + delta))
        view_start_var.set(new_val)
        state["dr_view_start"] = new_val
        refresh_all()

    ttk.Button(dr_top, text="<<", width=3,
               command=lambda: shift_view(-16)).pack(side="left", padx=2)
    ttk.Button(dr_top, text="<", width=2,
               command=lambda: shift_view(-4)).pack(side="left")
    sb = ttk.Spinbox(dr_top, from_=0, to=112, increment=4, width=5,
                     textvariable=view_start_var,
                     command=lambda: (state.update(dr_view_start=view_start_var.get()),
                                      refresh_all()))
    sb.pack(side="left", padx=4)
    ttk.Button(dr_top, text=">", width=2,
               command=lambda: shift_view(4)).pack(side="left")
    ttk.Button(dr_top, text=">>", width=3,
               command=lambda: shift_view(16)).pack(side="left", padx=2)

    label_var = tk.StringVar(value="")
    ttk.Label(dr_top, textvariable=label_var,
              foreground="#666").pack(side="left", padx=10)

    dr_grid = ttk.Frame(dr_frame)
    dr_grid.pack(padx=10, pady=6)

    # ---- DOLNY pasek z akcjami ----
    bottom = ttk.Frame(win)
    bottom.pack(fill="x", padx=10, pady=8)

    def reset_layout(first_note):
        nonlocal midi_map
        midi_map = {first_note + n - 1: n
                    for n in sorted(mpc_pad_map.keys())
                    if 0 <= first_note + n - 1 <= MAX_NOTE}
        state["selected_inst"] = None
        state["dr_view_start"] = max(0, (first_note // 4) * 4)
        view_start_var.set(state["dr_view_start"])
        refresh_all()

    ttk.Button(bottom, text="Reset 1:1 (start C1=36)",
               command=lambda: reset_layout(36)).pack(side="left", padx=2)
    ttk.Button(bottom, text="Reset 1:1 (start E3=64)",
               command=lambda: reset_layout(64)).pack(side="left", padx=2)
    ttk.Button(bottom, text="Reset 1:1 (start C3=60)",
               command=lambda: reset_layout(60)).pack(side="left", padx=2)
    ttk.Button(bottom, text="Wyczyść wszystkie",
               command=lambda: (midi_map.clear(),
                                state.update(selected_inst=None),
                                refresh_all())).pack(side="left", padx=2)

    ttk.Button(bottom, text="Anuluj",
               command=win.destroy).pack(side="right", padx=2)

    def save_and_close():
        on_save_callback(dict(midi_map), dict(mute_map))
        win.destroy()

    ttk.Button(bottom, text="Zapisz i generuj .adg",
               command=save_and_close).pack(side="right", padx=2)

    # ---- Refresh function ----
    def refresh_all():
        bank = bank_var.get()
        view_start = view_start_var.get()
        state["dr_view_start"] = view_start
        sel = state["selected_inst"]

        # Czyść siatki
        for w in mpc_grid.winfo_children():
            w.destroy()
        for w in dr_grid.winfo_children():
            w.destroy()

        # MPC bank: 4x4, układ MPC X/Live
        # row 0 = top of MPC = pads 13-16 (numbered within bank)
        # row 3 = bottom = pads 1-4
        mpc_layout = [
            [13, 14, 15, 16],
            [9, 10, 11, 12],
            [5, 6, 7, 8],
            [1, 2, 3, 4],
        ]
        # Cache buttonów - pozwala szybko aktualizować kolory bez rebuildu
        state["mpc_btns"] = {}  # inst -> btn
        state["dr_btns"] = {}   # midi -> btn

        for r, row in enumerate(mpc_layout):
            for c, pad_in_bank in enumerate(row):
                inst = bank * 16 + pad_in_bank
                sample = mpc_pad_map.get(inst, "")
                has_sample = bool(sample)
                assigned_to_midi = next((m for m, i in midi_map.items()
                                          if i == inst), None)
                mg = mute_map.get(inst, 0) if mute_map else 0
                btn_label = (f"P{inst}\n"
                             f"{sample[:14] if sample else '(puste)'}")
                if assigned_to_midi is not None:
                    btn_label += f"\n→MIDI {assigned_to_midi}"
                if mg:
                    btn_label += f"\n[ MG {mg} ]"
                bg = inst_color(inst,
                                selected=(sel == inst),
                                assigned=(assigned_to_midi is not None))
                if not has_sample:
                    bg = "#fafafa"
                btn = tk.Button(
                    mpc_grid, text=btn_label, width=14, height=4,
                    bg=bg, fg="#000" if has_sample else "#999",
                    state="normal" if has_sample else "disabled",
                    command=lambda i=inst: select_mpc(i),
                    highlightthickness=0,
                )
                btn.bind("<Button-2>", lambda e, i=inst: edit_mute_group(i))
                btn.bind("<Button-3>", lambda e, i=inst: edit_mute_group(i))
                btn.bind("<Control-Button-1>", lambda e, i=inst: edit_mute_group(i))
                btn.grid(row=r, column=c, padx=2, pady=2)
                state["mpc_btns"][inst] = btn

        # Drum Rack: 4x4, bottom-left = view_start MIDI
        for r in range(4):
            for c in range(4):
                midi = view_start + (3 - r) * 4 + c
                inst_at = midi_map.get(midi)
                sample = mpc_pad_map.get(inst_at, "") if inst_at else ""
                empty_pad = False
                if midi > MAX_NOTE:
                    label = ""
                    bg = "#cccccc"
                    btn_state = "disabled"
                elif inst_at is not None:
                    label = f"MIDI {midi}\nP{inst_at}\n{sample[:12]}"
                    mg_dr = mute_map.get(inst_at, 0) if mute_map else 0
                    if mg_dr:
                        label += f"\n[ MG {mg_dr} ]"
                    bg = inst_color(inst_at,
                                     selected=(sel == inst_at),
                                     assigned=True)
                    btn_state = "normal"
                else:
                    label = f"MIDI {midi}\n(puste)"
                    bg = "#fafafa"
                    btn_state = "normal"
                    empty_pad = True

                btn = tk.Button(
                    dr_grid, text=label, width=14, height=4, bg=bg,
                    fg="#999" if empty_pad else "#000",
                    state=btn_state,
                    command=lambda m=midi: click_dr(m),
                    highlightthickness=0,
                )
                btn.bind("<Double-Button-1>", lambda e, m=midi: unassign_dr(m))
                # Prawy klik / Ctrl+klik na pad z samplem -> edytuj MG sampla
                def _edit_mg_at(m):
                    inst_at = midi_map.get(m)
                    if inst_at is not None:
                        edit_mute_group(inst_at)
                btn.bind("<Button-2>", lambda e, m=midi: _edit_mg_at(m))
                btn.bind("<Button-3>", lambda e, m=midi: _edit_mg_at(m))
                btn.bind("<Control-Button-1>", lambda e, m=midi: _edit_mg_at(m))
                btn.grid(row=r, column=c, padx=2, pady=2)
                state["dr_btns"][midi] = btn

        label_var.set(f"({view_start} → {min(view_start + 15, MAX_NOTE)})")

    def _mpc_btn_bg(inst):
        sample = mpc_pad_map.get(inst, "")
        if not sample:
            return "#fafafa"
        sel = state["selected_inst"]
        assigned = next((m for m, i in midi_map.items() if i == inst), None)
        return inst_color(inst, selected=(sel == inst),
                          assigned=(assigned is not None))

    def _dr_btn_bg(midi):
        inst_at = midi_map.get(midi)
        if midi > MAX_NOTE:
            return "#cccccc"
        if inst_at is None:
            return "#fafafa"
        sel = state["selected_inst"]
        return inst_color(inst_at, selected=(sel == inst_at), assigned=True)

    def update_selection_colors():
        """Szybka aktualizacja kolorów bez pełnego refresh_all."""
        for inst, btn in state.get("mpc_btns", {}).items():
            try:
                btn.configure(bg=_mpc_btn_bg(inst))
            except Exception:
                pass
        for midi, btn in state.get("dr_btns", {}).items():
            try:
                btn.configure(bg=_dr_btn_bg(midi))
            except Exception:
                pass

    FLASH_BG = "#ff7043"  # pomarańczowy "playing"
    FLASH_MS = 180

    def flash_pad(inst):
        """Krótkie podświetlenie pada (MPC + drum rack jeśli przypisany)."""
        mb = state.get("mpc_btns", {}).get(inst)
        dr_midi = next((m for m, n in midi_map.items() if n == inst), None)
        db = state.get("dr_btns", {}).get(dr_midi) if dr_midi is not None else None
        for btn in (mb, db):
            if btn is not None:
                try:
                    btn.configure(bg=FLASH_BG)
                except Exception:
                    pass
        def revert():
            # Liczy aktualny kolor w momencie reverta (bezpieczne przy nakładających się flashach)
            if mb is not None:
                try:
                    mb.configure(bg=_mpc_btn_bg(inst))
                except Exception:
                    pass
            if db is not None and dr_midi is not None:
                try:
                    db.configure(bg=_dr_btn_bg(dr_midi))
                except Exception:
                    pass
        win.after(FLASH_MS, revert)

    def select_mpc(inst):
        play_sample(inst)
        flash_pad(inst)
        state["selected_inst"] = inst
        update_selection_colors()

    def edit_mute_group(inst):
        from tkinter import simpledialog
        cur = mute_map.get(inst, 0)
        new = simpledialog.askinteger(
            "Mute Group / Choke Group",
            f"Pad P{inst} ({mpc_pad_map.get(inst, '')[:30]})\n"
            f"Mute Group (0 = brak, 1-16 = grupa cichnięcia):",
            parent=win, initialvalue=cur, minvalue=0, maxvalue=16,
        )
        if new is None:
            return
        if new == 0:
            mute_map.pop(inst, None)
        else:
            mute_map[inst] = new
        # Refresh całej siatki MPC żeby pokazać nowe etykiety MG
        refresh_all()
        status_msg["set"](f"P{inst}: MuteGroup = {new}")

    def unassign_dr(midi):
        if midi in midi_map:
            del midi_map[midi]
            refresh_all()

    def click_dr(midi):
        sel = state["selected_inst"]
        if sel is None:
            inst_at = midi_map.get(midi)
            if inst_at is not None:
                play_sample(inst_at)
                flash_pad(inst_at)
            return
        for m in [k for k, v in midi_map.items() if v == sel]:
            del midi_map[m]
        midi_map[midi] = sel
        state["selected_inst"] = None
        refresh_all()

    # Pasek statusu (pokazuje co gra / błąd)
    status_var = tk.StringVar(value="Gotowy. Klawisze: 1234/qwer/asdf/zxcv")
    status_label = ttk.Label(win, textvariable=status_var,
                             foreground="#0277bd", anchor="w")
    status_label.pack(side="bottom", fill="x", padx=10, pady=4)
    status_msg["set"] = lambda msg: status_var.set(msg)

    # Klawiatura w stylu MPC (1234/qwer/asdf/zxcv) - zaznacz pad bieżącego banku
    keymap = {
        "1": 13, "2": 14, "3": 15, "4": 16,
        "q": 9,  "w": 10, "e": 11, "r": 12,
        "a": 5,  "s": 6,  "d": 7,  "f": 8,
        "z": 1,  "x": 2,  "c": 3,  "v": 4,
    }
    def on_key(event):
        # Ignoruj jeśli focus jest w polu tekstowym
        focused = win.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox)):
            return
        pad_in_bank = keymap.get(event.keysym.lower())
        if pad_in_bank is None:
            return
        inst = bank_var.get() * 16 + pad_in_bank
        if inst in mpc_pad_map:
            select_mpc(inst)
        else:
            status_msg["set"](f"P{inst}: brak pada")

    # bind_all żeby zadziałało niezależnie od tego gdzie jest focus.
    # Czyszczenie binda gdy okno się niszczy (każda ścieżka zamknięcia: X,
    # Anuluj, Zapisz - wszystko wywoła <Destroy>).
    win.bind_all("<Key>", on_key)
    def _on_destroy(event):
        if event.widget is win:
            try:
                win.unbind_all("<Key>")
            except Exception:
                pass
    win.bind("<Destroy>", _on_destroy)

    refresh_all()
    win.transient(parent_root)
    win.grab_set()
    win.focus_set()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as e:
        print(f"Brak tkinter: {e}", file=sys.stderr)
        return 2

    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
        root = TkinterDnD.Tk()
        dnd_available = True
    except ImportError:
        root = tk.Tk()
        dnd_available = False

    root.title("MPC -> Ableton Drum Rack (1:1)")
    root.geometry("680x600")
    root.minsize(580, 520)

    state = {
        "xpm_files": [],
        "samples_src": None,
        "output_dir": None,
        "custom_layouts": {},  # {xpm_path: midi_map}
        "custom_mutes": {},    # {xpm_path: mute_map} - nadpisuje XPM MuteGroup
    }

    pad = {"padx": 10, "pady": 6}

    # ---- 1. Pliki .xpm ----
    f1 = ttk.LabelFrame(root, text="1. Plik(i) .xpm")
    f1.pack(fill="both", expand=False, **pad)

    drop_label = tk.Label(
        f1,
        text=("Przeciągnij tu pliki .xpm (lub folder)" if dnd_available
              else "Kliknij 'Wybierz pliki .xpm'\n"
                   "(zainstaluj tkinterdnd2 dla drag-and-drop)"),
        bg="#f0f0f0", fg="#444", height=4, relief="ridge", bd=1,
        anchor="center", justify="center",
    )
    drop_label.pack(fill="x", padx=8, pady=6)

    list_frame = ttk.Frame(f1)
    list_frame.pack(fill="x", padx=8, pady=4)
    xpm_listbox = tk.Listbox(list_frame, height=4)
    xpm_listbox.pack(side="left", fill="x", expand=True)
    sb = ttk.Scrollbar(list_frame, orient="vertical", command=xpm_listbox.yview)
    sb.pack(side="right", fill="y")
    xpm_listbox.config(yscrollcommand=sb.set)

    def refresh_xpm_list():
        xpm_listbox.delete(0, "end")
        for p_ in state["xpm_files"]:
            xpm_listbox.insert("end", str(p_))

    def add_xpm_files(files):
        first_xpm = None
        for f in files:
            p_ = Path(f).expanduser()
            if p_.is_dir():
                xpms = sorted(p_.rglob("*.xpm"))
                for x in xpms:
                    if x not in state["xpm_files"]:
                        state["xpm_files"].append(x)
                        if first_xpm is None:
                            first_xpm = x
            elif p_.suffix.lower() == ".xpm" and p_.is_file():
                if p_ not in state["xpm_files"]:
                    state["xpm_files"].append(p_)
                    if first_xpm is None:
                        first_xpm = p_
        refresh_xpm_list()
        # Auto-uzupełnij folder sampli jeśli pusty - sample MPC są zwykle obok .xpm
        if first_xpm and not samples_var.get().strip():
            parent = first_xpm.parent
            samples_var.set(str(parent))
            state["samples_src"] = parent

    def pick_xpm():
        files = filedialog.askopenfilenames(
            title="Wybierz pliki .xpm",
            filetypes=[("MPC Program", "*.xpm"), ("Wszystkie", "*.*")],
        )
        if files:
            add_xpm_files(files)

    btns1 = ttk.Frame(f1)
    btns1.pack(fill="x", padx=8, pady=4)
    ttk.Button(btns1, text="Wybierz pliki .xpm", command=pick_xpm).pack(side="left")
    ttk.Button(btns1, text="Wyczyść",
               command=lambda: (state["xpm_files"].clear(),
                                state["custom_layouts"].clear(),
                                state["custom_mutes"].clear(),
                                refresh_xpm_list())
               ).pack(side="left", padx=6)

    def open_editor_for_selected():
        """Otwiera Pad Layout Editor dla zaznaczonego (lub pierwszego) pliku .xpm."""
        if not state["xpm_files"]:
            messagebox.showwarning("Brak pliku", "Najpierw dodaj plik .xpm.")
            return
        sel_idx = xpm_listbox.curselection()
        idx = sel_idx[0] if sel_idx else 0
        xpm_path = state["xpm_files"][idx]
        try:
            _, pad_map_names, pad_mute_groups = parse_xpm(xpm_path)
        except Exception as e:
            messagebox.showerror("Błąd parsowania", f"{xpm_path.name}: {e}")
            return
        if not pad_map_names:
            messagebox.showwarning("Pusto",
                                    f"{xpm_path.name} nie ma żadnych pad-ów z samplami.")
            return

        def on_save(midi_map, mute_map):
            state["custom_layouts"][xpm_path] = midi_map
            state["custom_mutes"][xpm_path] = mute_map
            mg_count = sum(1 for v in mute_map.values() if v > 0)
            log(f"Zapisano układ {xpm_path.name}: "
                f"{len(midi_map)} pozycji, {mg_count} padów z MuteGroup")

        existing_layout = state["custom_layouts"].get(xpm_path)
        # Użyj edytowanego mute_map jeśli istnieje, w innym razie z XPM
        existing_mutes = state["custom_mutes"].get(xpm_path, pad_mute_groups)
        sd = samples_var.get().strip()
        samples_dir = Path(sd).expanduser() if sd else None
        open_layout_editor(root, pad_map_names, on_save, existing_layout,
                           samples_src_dir=samples_dir,
                           mute_map=existing_mutes)

    ttk.Button(btns1, text="Otwórz Pad Layout Editor...",
               command=open_editor_for_selected
               ).pack(side="left", padx=6)

    # ---- 2. Folder ze źródłowymi samplami ----
    f2 = ttk.LabelFrame(root, text="2. Folder ze źródłowymi samplami (.wav)")
    f2.pack(fill="x", expand=False, **pad)
    samples_var = tk.StringVar(value="")
    ttk.Entry(f2, textvariable=samples_var).pack(side="left", fill="x",
                                                  expand=True, padx=8, pady=8)

    def pick_samples():
        d = filedialog.askdirectory(title="Wybierz folder ze samplami")
        if d:
            samples_var.set(d)
            state["samples_src"] = Path(d)

    ttk.Button(f2, text="Wybierz folder...", command=pick_samples
               ).pack(side="right", padx=8, pady=8)

    # ---- 3. Folder wyjściowy ----
    f3 = ttk.LabelFrame(root, text="3. Folder wyjściowy (gdzie powstaną kity)")
    f3.pack(fill="x", expand=False, **pad)
    output_var = tk.StringVar(value="")
    ttk.Entry(f3, textvariable=output_var).pack(side="left", fill="x",
                                                 expand=True, padx=8, pady=8)

    def pick_output():
        d = filedialog.askdirectory(title="Wybierz folder wyjściowy")
        if d:
            output_var.set(d)
            state["output_dir"] = Path(d)

    ttk.Button(f3, text="Wybierz folder...", command=pick_output
               ).pack(side="right", padx=8, pady=8)

    # ---- 4. Opcje ----
    f4 = ttk.LabelFrame(root, text="4. Opcje (zaawansowane)")
    f4.pack(fill="x", expand=False, **pad)

    auto_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(f4, text="Auto-mapowanie nut (zalecane)",
                    variable=auto_var).pack(side="left", padx=8, pady=8)
    ttk.Label(f4, text="Pierwsza nuta:").pack(side="left", padx=8)
    fn_var = tk.IntVar(value=DEFAULT_FIRST_NOTE)
    fn_spin = ttk.Spinbox(f4, from_=0, to=120, textvariable=fn_var, width=5)
    fn_spin.pack(side="left")
    ttk.Label(f4, text="(36 = C1)").pack(side="left", padx=4)
    split_banks_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(f4, text="Podziel banki MPC na osobne drum racki",
                    variable=split_banks_var).pack(side="left", padx=20, pady=8)

    # ---- Log ----
    f5 = ttk.LabelFrame(root, text="Log")
    f5.pack(fill="both", expand=True, **pad)
    log_text = tk.Text(f5, height=10, wrap="word")
    log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def log(msg: str):
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        root.update_idletasks()

    def do_convert():
        if not state["xpm_files"]:
            messagebox.showwarning("Brak plików", "Dodaj pliki .xpm.")
            return
        sd = samples_var.get().strip()
        od = output_var.get().strip()
        if not sd:
            messagebox.showwarning("Brak folderu", "Wybierz folder ze samplami.")
            return
        if not od:
            messagebox.showwarning("Brak folderu", "Wybierz folder wyjściowy.")
            return
        sd_path = Path(sd).expanduser()
        od_path = Path(od).expanduser()
        if not sd_path.is_dir():
            messagebox.showerror("Błąd", f"Folder źródłowy nie istnieje: {sd_path}")
            return
        od_path.mkdir(parents=True, exist_ok=True)

        log_text.delete("1.0", "end")
        ok, fail = 0, 0
        first_note = None if auto_var.get() else fn_var.get()
        split_banks = split_banks_var.get()
        for xpm in list(state["xpm_files"]):
            try:
                log(f"== {xpm.name} ==")
                custom_map = state["custom_layouts"].get(xpm)
                custom_mute = state["custom_mutes"].get(xpm)
                if custom_map:
                    log(f"  (układ z edytora: {len(custom_map)} pozycji)")
                if custom_mute:
                    mg_count = sum(1 for v in custom_mute.values() if v > 0)
                    if mg_count:
                        log(f"  (custom MuteGroup: {mg_count} padów)")
                if split_banks:
                    fn_split = first_note if first_note is not None else DEFAULT_FIRST_NOTE
                    results = convert_split_banks(
                        xpm, sd_path, od_path,
                        first_note=fn_split,
                        midi_map=custom_map,
                        mute_map=custom_mute,
                        progress=log,
                    )
                    for res in results:
                        log(f"  -> {res.kit_dir.name}/{res.adg.name} "
                            f"(pady: {res.placed}, brak: {len(res.missing)})")
                    ok += len(results)
                else:
                    res = convert_one(xpm, sd_path, od_path,
                                      first_note=first_note,
                                      midi_map=custom_map,
                                      mute_map=custom_mute,
                                      progress=log)
                    log(f"  -> {res.kit_dir.name}/{res.adg.name}")
                    log(f"  Pady: {res.placed}, brak: {len(res.missing)}, "
                        f"ucięte: {res.truncated}")
                    if res.missing:
                        log(f"  Brakujące: " + ", ".join(res.missing[:5]) +
                            (" ..." if len(res.missing) > 5 else ""))
                    ok += 1
            except Exception as e:
                log(f"  BŁĄD: {e}")
                fail += 1
        log(f"\nGotowe: {ok} OK, {fail} błędów")
        messagebox.showinfo("Konwersja zakończona",
                            f"OK: {ok}\nBłędy: {fail}\n\nKity zapisane w:\n{od_path}")

    ttk.Button(root, text="Konwertuj",
               command=do_convert).pack(fill="x", padx=10, pady=10)

    # ---- Drag and drop ----
    if dnd_available:
        def on_drop(event):
            raw = event.data
            paths = []
            buf = ""
            in_brace = False
            for ch in raw:
                if ch == "{":
                    in_brace = True
                elif ch == "}":
                    in_brace = False
                    if buf:
                        paths.append(buf); buf = ""
                elif ch == " " and not in_brace:
                    if buf:
                        paths.append(buf); buf = ""
                else:
                    buf += ch
            if buf:
                paths.append(buf)
            add_xpm_files(paths)

        drop_label.drop_target_register(DND_FILES)
        drop_label.dnd_bind("<<Drop>>", on_drop)

    log(f"Gotowy. Drag-and-drop: {'TAK' if dnd_available else 'NIE'}")
    log("Tryb 1:1: każdy MPC pad N -> drum rack pad pozycja N. "
        "Pierwsza warstwa per pad.")
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    if len(sys.argv) == 1:
        return run_gui()
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
