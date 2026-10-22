# Upgrade from the first version (u1) to u2 and the HUD

For a server that runs the images from the first merge (PR #1):

- `openjarvis-unraid:0.0.1.dev88-cf464de-u1`
- `cognee-unraid:1.6.5-u1`

After this upgrade it runs:

- `openjarvis-unraid:0.0.1.dev88-cf464de-u2`;
- `cognee-unraid:1.6.5-u2`;
- the new `machine-hud:0.2.0`;
- the custom package (Finch and Root, intake, council) from branch `home-jarvis`.

All data stays where it is: the API keys, `config.toml`, the databases, the
vault and Cognee's stores. The upgrade only changes image tags and adds files.
About 20 minutes, most of it waiting for pulls.

## What changes

| Container | u1 to u2 |
|---|---|
| openjarvis | Startup hooks for custom code in `/data/custom` (the floor, intake and council load through them). Config and data unchanged. |
| cognee | Keyless ingestion with local GLiNER, Mistral support, the embedding model fetched at start, a fixed `cognee-ingest` message. Stores unchanged. |
| machine-hud | New. |

## 0. Back up

```bash
docker exec openjarvis jarvis-backup
```

Optional, and safe while the containers run, since both folders are read and
nothing is written to them:

```bash
tar czf /mnt/user/TheMachine/_backups/appdata-before-u2.tgz \
  -C /mnt/cache/appdata openjarvis cognee
```

## 1. Publish the images (GitHub)

Images are built and pushed only from `main`. Merge PR #2: mark it ready for
review, then merge. Wait for the `unraid-image` workflow on `main` to go
green, about 10 minutes. It pushes:

- `openjarvis-unraid:0.0.1.dev88-cf464de-u2`
- `cognee-unraid:1.6.5-u2`
- `machine-hud:0.2.0`

`machine-hud` is a new package and may be private. If Unraid later says
"denied" when pulling it, go to GitHub, Packages, machine-hud, Package
settings, and set the visibility to public (the other two already pull).

## 2. Cognee

Docker tab, `cognee`, Edit:

1. Repository: change `-u1` to `-u2`.
2. If `ANTHROPIC_API_KEY` or `MISTRAL_API_KEY` is set: put `reference/chats`
   in `COGNEE_EXCLUDE`. Chats with other people are filed there from now on,
   and cognify would send them to the extraction model. With no key (local
   GLiNER) nothing leaves the server, so this is not needed.
3. Optional new variable, via "Add another Path, Port, Variable":
   `GRAPH_EXTRACTOR`. Leave it unset for automatic (the model when a key is
   set, else local); `gliner_demo` forces local.
4. Apply. First start after the upgrade downloads the embedding model once.

Check: `docker logs cognee | tail -20` shows the API up; the health icon turns
green.

## 3. The custom package (Unraid terminal)

The fork is public, so a plain download works.

```bash
cd /tmp && rm -rf hj && mkdir hj && cd hj
wget -qO hj.tgz https://github.com/BGameiro2000/OpenJarvis/archive/refs/heads/home-jarvis.tar.gz
tar xzf hj.tgz
SRC=/tmp/hj/OpenJarvis-home-jarvis/home-jarvis
CUSTOM=/mnt/cache/appdata/openjarvis/custom

mkdir -p "$CUSTOM"
rm -rf "$CUSTOM/home_jarvis"
cp -r "$SRC/home_jarvis" "$CUSTOM/"
[ -f "$CUSTOM/floor.toml" ]  || cp "$SRC/floor.example.toml"  "$CUSTOM/floor.toml"
[ -f "$CUSTOM/intake.toml" ] || cp "$SRC/intake.example.toml" "$CUSTOM/intake.toml"
chown -R 99:100 "$CUSTOM"

# Finch and Root SOUL files. An existing one is kept as SOUL.md.bak.
for p in finch root; do
  d=/mnt/user/TheMachine/personas/$p
  mkdir -p "$d"
  [ -f "$d/SOUL.md" ] && cp "$d/SOUL.md" "$d/SOUL.md.bak"
  cp "$SRC/vault/personas/$p/SOUL.md" "$d/SOUL.md"
done
chown -R 99:100 /mnt/user/TheMachine/personas

rm -rf /tmp/hj
```

`intake.toml` starts with `dry_run = true`: nothing in the inbox moves until
you press Apply in the HUD or change it.

## 4. OpenJarvis

Docker tab, `openjarvis`, Edit:

1. Repository: change `-u1` to `-u2`.
2. `OPENJARVIS_CUSTOM_MODULES`: `home_jarvis`.
3. Optional, for a first look with no model cost: add the variable
   `HOME_JARVIS_DEMO` = `1` (scripted Finch, Root, council and classifier).
   Remove it afterwards.
4. Apply.

Check:

```bash
docker logs openjarvis 2>&1 | grep home_jarvis
```

Expected: `floor ready: profile home_text, ...` and `intake ready: ...`.

Cost note: the `home_text` profile includes the `council` mode, which adds
three model calls on decision questions ("should I", an "or", a price).
Remove `council` from the profile in `floor.toml`, or switch it off in the
HUD, if you do not want that.

## 5. The HUD (new container)

Unraid terminal, to add the template:

```bash
wget -qO /boot/config/plugins/dockerMan/templates-user/my-machine-hud.xml \
  https://raw.githubusercontent.com/BGameiro2000/OpenJarvis/main/deploy/unraid/templates/machine-hud.xml
cat /mnt/cache/appdata/openjarvis/.api_key; echo
cat /mnt/cache/appdata/cognee/.api_key; echo
```

Docker tab, Add Container, Template: `machine-hud`. Fill in:

- `OPENJARVIS_API_KEY` and `COGNEE_API_KEY`: the two keys printed above.
- `HUD_PASSWORD`: your choice. Or `HUD_AUTH=tailscale` if you publish it
  with `tailscale serve`.
- `HUD_DEMO`: `1` for the first look, then `0`.
- Network: `jarvis-net`. Port: 8080.

Apply, then open `http://<server>:8080`.

## 6. Verify

Run S12 and S13 in [SERVER-TESTS.md](SERVER-TESTS.md). The short version:

- The top bar shows OJ, COGNEE and FLOOR in green, mode FLOOR.
- A "should I buy X now?" question gives COUNCIL lines and maybe Root cutting in.
- Memory search returns hits.
- Intake shows the inbox, with Dry run.

## Roll back

Edit each container and set the tag back to `-u1`; the u1 images stay on
GHCR. Clear `OPENJARVIS_CUSTOM_MODULES`: the u1 image has no startup hooks,
so the package would only be imported and do nothing. Stop or remove `machine-hud`. Nothing in the data needs undoing; the
SOUL files you replaced are next to them as `SOUL.md.bak`.
