# Publishing this repo

Everything is ready to push. Run these from the project directory once you're happy.

## 1. Create the repo and push

```bash
gh repo create Efendiminati/midi-bpm-fixer \
  --public \
  --description "Change a MIDI file's BPM without moving the notes — fixes the 120 BPM stamp from Sonic Visualiser and transcription tools. Windows GUI + CLI." \
  --source . --remote origin --push
```

## 2. Set the topics (this is how anyone finds it)

```bash
gh repo edit Efendiminati/midi-bpm-fixer --add-topic \
midi,bpm,tempo,midi-editor,music-production,sonic-visualiser,synthv,synthesizer-v,vocaloid,utau,transcription,quantization,audio-to-midi,python,tkinter
```

Topics carry more weight than the description for discovery — GitHub's topic pages are
where a search like "change midi bpm" lands.

## 3. Cut the release and attach the .exe

```bash
gh release create v1.0.0 "dist/MIDI BPM Fixer.exe" \
  --title "MIDI BPM Fixer 1.0.0" \
  --notes "First release.

- Re-grid: change the stated BPM and rescale ticks so notes keep their exact timing
- Re-time: change playback speed
- Optional time signature (Sonic Visualiser omits it)
- Batch a whole folder
- Windows .exe, no Python required

Verified with the built-in selftest: note onsets unchanged to 0.00000s after conversion."
```

## Checklist before pushing

- [x] `python midi_bpm_fixer.py --selftest` — all checks pass
- [x] `dist/MIDI BPM Fixer.exe --selftest` — exit 0
- [x] README has the screenshot, both modes, CLI examples, limitations
- [x] MIT LICENSE in place (`Efendiminati`, 2026 — edit if you want a different name)
- [x] `.gitignore` excludes `build/`, `dist/`, `*.spec`, converted sample outputs
- [ ] Decide whether the sample `.mid` stays in the repo (it does by default, harmless)

## After publishing

The honest expectation: this is a small niche. Discovery comes from the topics, the exe in
Releases, and people finding it when they search the exact problem. Nothing to do about that
beyond having the words they type in the README.
