| Action | How |
|---|---|
| Open or close the book | Click it |
| Move her | Drag the book. The open and closed book each remember their own spot. |
| Lay the closed book sideways, or stand it back up | Right-click it |
| Type a message | `Mod+Shift+A`, then Enter. Esc releases the keyboard. |
| Talk | `Mod+Shift+S`. She stops listening when you go quiet. Press again to stop sooner or to interrupt her. |
| Close and cancel everything | `Mod+Shift+X` |
| Put the typing box above her replies, or back below | `qs -p shell ipc call alice togglePrompt`. The choice is remembered; bind it to a key in niri if you like. |
| Change her outfit | Ask her ("put on your queen outfit", "go evil", "change back into your normal dress"), or `qs -p shell ipc call alice toggleOutfit evil` (or `queen`) to put it on and take it off again. |

Spoken questions get spoken answers. Typed questions stay silent.

Try "Remember that my browser is Zen", then "Open my browser".
