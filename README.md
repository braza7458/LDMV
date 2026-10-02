# LDMV — Montage vidéo avec coupe automatique des silences

Logiciel de montage inspiré de CapCut, en Python (PySide6 + ffmpeg), avec ce
que CapCut ne sait pas faire : **supprimer tous les silences d'une piste en
une seule opération**.

## Démarrage rapide (sans terminal)

Seul [Python](https://www.python.org/downloads/) ≥ 3.10 est nécessaire
(sous Windows, cochez « Add Python to PATH » pendant son installation).
ffmpeg est installé automatiquement.

1. Sur GitHub, bouton vert **Code** → **Download ZIP**, puis décompressez le dossier.
2. Double-cliquez sur :
   - **Windows** : `Lancer LDMV (Windows).bat`
   - **Mac** : `Lancer LDMV (Mac-Linux).command`
     (si macOS le bloque : clic droit → **Ouvrir** → **Ouvrir**)
3. La première fois, l'installation prend quelques minutes, puis le logiciel s'ouvre.
   Les fois suivantes, il s'ouvre directement.

Astuce Windows : glissez une vidéo sur le fichier `.bat` pour l'ouvrir directement.

## Installation pour développeurs

```bash
pip install -e ".[ui,dev]"
```

## Utilisation

```bash
ldmv                      # interface graphique (ou : python -m ldmv)
ldmv video.mp4            # ouvre directement un fichier

# Sans interface : coupe tous les silences et exporte
ldmv-silence cours.mp4 -o cours_court.mp4 --threshold -40 --min-silence 500 --padding 100
ldmv-silence cours.mp4 --dry-run   # liste seulement les silences détectés
```

### Raccourcis

| Touche | Action |
|---|---|
| **Espace** | Lecture / Pause (saute les passages coupés) |
| **B** | Outil Ciseau (clic sur un clip = coupe) |
| A / V | Outil Sélection |
| Ctrl+B | Diviser à la tête de lecture |
| Q / W | Supprimer à gauche / à droite de la tête de lecture |
| Suppr / Retour arrière | Supprimer la sélection |
| N | Aimant de piste principale |
| S | Alignement automatique |
| Ctrl+Maj+S | Supprimer les silences… |
| Ctrl+Z / Ctrl+Maj+Z | Annuler / Rétablir |
| ← / → (Maj) | Image précédente / suivante (± 1 s) |
| Ctrl + molette, Ctrl+= / Ctrl+- | Zoom |

## Les trois fonctionnalités

### 1. Coupe automatique des silences — `ldmv/core/silence.py`

1. Le signal est découpé en fenêtres de 20 ms et le niveau RMS de chacune est
   converti en dBFS : `dB = 20·log10(RMS)`.
2. Une fenêtre est silencieuse si `dB < seuil` (−40 dB par défaut).
3. Les sons isolés plus courts que 60 ms (clics) au milieu d'un silence sont ignorés.
4. Seuls les silences d'au moins `min_silence_ms` sont gardés.
5. Une marge (`padding_ms`) est rendue autour de la parole pour ne pas couper les mots.

`Editor.remove_silences` (`ldmv/core/edits.py`) coupe au début et à la fin de
chaque silence puis supprime tous les segments **en une seule opération** :
un seul Ctrl+Z restaure tout. Avec l'aimant activé, les clips restants sont
recollés. Dans l'interface, les zones qui seront supprimées s'affichent en
rouge en direct pendant que vous réglez le seuil : l'analyse audio est faite
une fois à l'import, le réglage est ensuite instantané, même sur 30 minutes.

### 2. Barre d'outils et magnétisme — `ldmv/ui/main_window.py`, `ldmv/ui/timeline_widget.py`

- **Sélection**, **Ciseau** (B), **Supprimer**, **Annuler/Rétablir**, **Diviser**.
- **Aimant de piste principale** : la piste principale reste collée bout à
  bout à partir de 0 ; supprimer, déplacer, rogner ou couper les silences
  referme automatiquement les vides. Désactivé, les clips gardent leur position.
- **Alignement automatique** : pendant un glisser, les bords s'accrochent aux
  bords des autres clips (toutes pistes) et à la tête de lecture (ligne jaune).

### 3. Roll edit (édition de frontière) — `roll_edit` dans `ldmv/core/edits.py`

En survolant la jonction entre deux clips adjacents, le curseur devient ⇔ ;
glisser déplace la coupe sans changer la durée totale.

Pour un clip gauche **G** et un clip droit **D** adjacents (`G.end == D.start`),
un déplacement de Δ (positif = vers la droite) donne :

```
G.trim_out' = G.trim_out + Δ      G s'allonge (Δ>0) ou raccourcit (Δ<0) par la fin
D.trim_in'  = D.trim_in  + Δ      D raccourcit (Δ>0) ou s'allonge (Δ<0) par le début
D.start'    = D.start    + Δ      la jonction suit la souris
G.start, D.end inchangés  →  (G.durée + Δ) + (D.durée − Δ) = constante
```

Δ est borné plutôt que refusé, pour que le geste reste fluide :

```
Δ_max = min(G.durée_source − G.trim_out,  D.durée − durée_min)
Δ_min = max(−(G.durée − durée_min),       −D.trim_in)
```

Autrement dit : G ne peut pas dépasser la fin de son fichier, D ne peut pas
remonter avant le début du sien, et aucun des deux ne descend sous une image.

## Architecture

```
ldmv/
  core/            logique pure, testée sans interface
    model.py       MediaSource, Clip (source_in/source_out = trim_in/trim_out), Track, Timeline
    silence.py     détection des silences (numpy)
    edits.py       ciseau, suppression, déplacement, rognage, roll edit, silences, historique
    timecode.py    temps en microsecondes entières (pas d'erreur d'arrondi)
  media/ffmpeg.py  sonde, décodage audio, forme d'onde, export
  ui/              PySide6 : fenêtre, timeline, lecteur, dialogue des silences, icônes
  cli.py           ldmv-silence
tests/             pytest
```

L'export concatène la piste principale avec ffmpeg (`trim`/`atrim` + `concat`,
script de filtres dans un fichier pour supporter des centaines de coupes).

## Tests

```bash
pytest
```

## Limites actuelles

- À chaque coupe, la lecture saute dans le fichier : une micro-coupure du son est possible ;
  l'export, lui, est parfaitement continu.
- L'export ne rend que la piste principale ; les vides (aimant désactivé) sont ignorés.
- Pas encore de sauvegarde de projet.
