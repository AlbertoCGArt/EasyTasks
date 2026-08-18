# Easy Tasks — LinkedIn and X

## How publishing actually works here

**There is no LinkedIn or X connector.** I searched the connector registry — neither
platform has one, and nothing on your account (Canva, Gmail, Google Calendar, Google
Drive) can post to them. So there's no API path from this session.

Two real options:

1. **Copy and paste.** For a launch post this is genuinely the right call — it takes
   ninety seconds, and you want to be looking at the preview when you hit post.
2. **I drive your browser.** Claude in Chrome can open LinkedIn or X in your logged-in
   Chrome, paste the text, attach the media and post. Say the word and I'll do it. It's
   slower than pasting it yourself and needs you to grant site permissions, so it's only
   worth it if you'd rather not touch it.

Either way the text below is ready.

---

## The one thing that matters more than the copy

**Both platforms suppress posts that send people off-platform, and both are worst about
YouTube links specifically.** A post whose payload is a YouTube URL will reach a
fraction of the audience of the same post with a native video.

So: **cut a 30–45 second clip from the video and upload it natively to each platform.**
Put the link in a reply (X) or the first comment (LinkedIn).

The clip almost writes itself — use **1:25 to 2:10**, where you assign the pipes and
then immediately reuse it for the second batch. It's the most visually satisfying stretch
in the whole video: cluttered outliner, two clicks, everything lands. No narration needed
if you'd rather cut it silent with a text overlay.

X allows up to 2:20 of video on a free account; LinkedIn allows up to 10 minutes. Both
autoplay muted, so whatever you cut has to read without sound.

---

## X

### Option A — single post

Post the clip with this. 276 characters, so it fits a free account with no room spare.

```
Two years of adding one small thing at a time, every time something in Blender should have been one click and took six.

Easy Tasks: select your pipes, click Pipes. They land in the right collection, inside the hierarchy you already built.

Free, open source, Blender 3.0-5.x.
```

Then **reply to your own post** with the link:

```
Download, full feature list and shortcuts: https://3dartstuff.com/easytasks/
```

Putting the URL in the reply rather than the post is the single highest-leverage thing
you can do here. Anyone who wants it will read one reply down.

### Option B — thread

Better if you want to show more than one feature. Attach the clip to post 1, and a
screenshot or the feature-grid image to post 3.

**1/4** — 270 chars

```
Two years of adding one small thing at a time, every time something in Blender should have been one click and took six.

That's Easy Tasks. Select your pipes, click Pipes — they land in the right collection, inside the hierarchy you already built.

Free and open source.
```

**2/4** — 210 chars

```
Point it at your project root and it uses your existing structure instead of inventing a new one beside it.

Clicking Floor drops the selection into PRODUCTION > MODULES > FLOOR. No new collection. No dragging.
```

**3/4** — 232 chars

```
It also colour-codes UV distortion as vertex colours right in the viewport — blue compressed, green even, red stretched — and finds objects sharing an identical UV layout before they reach a bake.

Neither has a built-in equivalent.
```

**4/4** — 148 chars

```
Blender 3.0-5.x. GPL-3.0-or-later, so modify it and ship commercial work with it.

Download and full feature list: https://3dartstuff.com/easytasks/
```

Note the `>` instead of `▸` and the hyphen in `3.0-5.x` — X's font stack renders both
more reliably, and the en dash sometimes triggers odd line breaks on mobile.

### On hashtags

Two or three, at the end, or none. X hashtags do very little now and more than three
reads as spam. If you use any: `#b3d #blender3d #gamedev`. `#b3d` is the one the Blender
community actually follows.

---

## LinkedIn

Only the first ~200 characters show before "...see more", so the opening has to earn the
click. This one opens on the two-year detail, which is the most human thing about the
project and the part that performs on LinkedIn specifically — it's a story about
persistence, which is the register that platform rewards.

**Post the clip natively. Put the link in the first comment, not the post body** —
LinkedIn measurably reduces reach on posts with external links.

```
Two years ago I started fixing my own annoyances in Blender. I've just released the result.

Every time I hit something in environment work that should have taken one click and took six, I added it to a small add-on. Never a big push — a feature here, a fix there, whenever something annoyed me enough.

It's called Easy Tasks, and it's free.

The part I use most: you select your pipes, click Pipes, and they land in the right collection. Point it at your project root and it works with the hierarchy you already built, rather than creating a new one beside it — which was the specific thing that used to send me back into the outliner to drag things around by hand.

There's more in it than that. It colour-codes UV distortion as vertex colours right in the viewport. It finds objects sharing an identical UV layout before they reach a bake. It batches every selected object out to its own FBX. None of that existed in Blender when I needed it.

The whole thing is free and open source under GPL-3.0-or-later. No licence to buy, nothing held back for a paid tier — modify it, ship commercial work with it, whatever you need.

Two years of small additions turns out to be quite a lot of add-on.

Link in the comments.

#blender #3d #gamedev #environmentart #b3d
```

**First comment:**

```
Download, full feature list and the shortcut reference: https://3dartstuff.com/easytasks/

It's open source, so if you find a bug there's a report link on that page.
```

LinkedIn hashtags still carry a little weight, unlike X's — three to five at the end is
right.

---

## One honest note on channel fit

LinkedIn and X are worth posting to, and the copy above is written to do well on both.
But for a free Blender add-on, neither is where your actual audience is.

The places this lands hardest:

- **r/blender and r/blenderhelp** — a demo GIF of the pipes assignment would do well.
  Read the self-promotion rules first; both subreddits allow free-tool posts but have
  specific framing requirements.
- **Blender Artists forum**, in Released Scripts and Themes. This is the canonical place
  a free add-on gets found, and posts there stay discoverable for years, which is not
  true of anything you post on X.
- **Blender Discord**, in the add-on channels.
- **The Blender Extensions platform** (extensions.blender.org). Listing there puts it
  inside Blender's own add-on browser, which is a permanent discovery channel rather
  than a one-day spike. Given the add-on is already GPL-3.0-or-later, it should qualify.

If you want, I'll write the Blender Artists post and the Extensions listing next — they
want a different register from either LinkedIn or X, more technical and less pitched.
