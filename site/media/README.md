# Pictures and clips for the website

`site/index.html` shows a placeholder for each file that isn't here yet. Capture them from a real desktop, crop to her corner, and keep them small (PNG under 500 KB, WebM clips under 4 MB, 16:10).

| File | What to show |
|---|---|
| `hero.webm` and `hero.png` (its first frame) | The closed book opening, Alice listening, and answering a spoken question with lip sync (about 10 seconds) |
| `talking.webm` and `talking.png` | A voice question and her spoken answer |
| `approval.png` | An Allow/Deny card for an action, like opening an app or running a command |
| `weather.png` | The week's forecast card |
| `moods.png` | A row of her faces for different moods |
| `wardrobe.png` | Her usual dress, Queen, Evil and a holiday outfit side by side |
| `phone.png` | The Android app in a conversation |

A clip from a screen recording can be made with, for example:

    wf-recorder -g "$(slurp)" -f clip.mp4
    ffmpeg -i clip.mp4 -vf "scale=960:-2,fps=24" -c:v libvpx-vp9 -b:v 0 -crf 36 -an hero.webm
    ffmpeg -i hero.webm -frames:v 1 hero.png

Only publish art you have the right to share.
