# Debug & Correctif — Workflow `recent-posts.yml`

> Documenté le 10 mars 2026 · Repo : `hugomrvt/hugomrvt`

---

## Contexte

Le README du profil GitHub contient une section **✦ Latest articles** mise à jour automatiquement deux fois par jour (06h00 et 18h00 UTC) via le workflow `.github/workflows/recent-posts.yml`. Ce workflow récupère les derniers articles publiés et injecte un tableau Markdown entre deux balises HTML commentées dans le README.

```
<!-- LATEST_ARTICLES_START -->
| ... tableau ... |
<!-- LATEST_ARTICLES_END -->
```

---

## Symptôme observé

Depuis plusieurs mois, la section affiche un tableau vide — uniquement les en-têtes, sans aucune ligne de données :

```markdown
| | Title | Date |
|----------|-------|------|
```

Pourtant, le workflow tourne correctement deux fois par jour et s'affiche en **succès** (✅) dans l'onglet Actions. Le commit "Update recent posts" est bien effectué régulièrement par `github-actions[bot]`.

---

## Diagnostic

### Méthode
Inspection des logs détaillés du run #435 (dernier run avant correction) via l'onglet Actions > update-posts > "Update README with recent posts".

### Logs révélateurs (run #435)

```
=== Starting article fetch ===
Substack URL: https://insights.genairadar.co
Medium feed: https://medium.com/feed/@hugomrvt

Substack API: 0 entries
Substack RSS: 0 entries
Substack HTML fallback: 0 entries
Feed unavailable: https://medium.com/feed/@hugomrvt
Medium: 0 entries
Warning: No entries from Medium — check for 403 errors

=== Final selection: 0 articles ===
README updated with latest posts
```

Le script récupère **0 articles** depuis toutes les sources, puis écrase quand même le README avec un tableau vide. Les deux sources sont en échec pour des raisons distinctes.

---

## Causes racines

### Cause 1 — Substack `insights.genairadar.co` est vide

`insights.genairadar.co` est un nouveau Substack (custom domain de `genairadar.substack.com`) configuré mais sans contenu publié — la homepage affiche "Coming Soon".

- `GET https://insights.genairadar.co/api/v1/archive?sort=new&offset=0&limit=10` → retourne `[]`
- `GET https://insights.genairadar.co/feed` → feed RSS sans entrées

Les articles GenAI Radar existants sont publiés sur **Medium** (via la publication `loop.genairadar.co`), pas sur ce Substack.

### Cause 2 — Medium bloque les requêtes depuis GitHub Actions (403 Forbidden)

Medium applique un blocage par plage d'IP sur les IP des runners GitHub Actions. Une requête vers `https://medium.com/feed/@hugomrvt` depuis un navigateur retourne le feed RSS complet, mais depuis un runner GitHub Actions retourne une erreur **403 Forbidden**.

### Cause 3 — Absence de garde-fou (bug critique)

Lorsque les deux sources échouent et retournent 0 articles, le script **écrasait quand même le README** avec un tableau vide. Ce comportement masquait le problème : le commit "Update recent posts" se produisait normalement, mais avec un contenu vide, effaçant les données précédemment correctes.

---

## Correctif appliqué (10 mars 2026)

Commit : `39f2c33` — *fix: rewrite recent-posts workflow - Medium only, guard against empty table, proxy fallback*

### 1. Suppression de la source Substack

La source `insights.genairadar.co` a été retirée du workflow. Le Substack étant vide ("Coming Soon"), il ne sert pas de source valide. Tous les articles sont actuellement sur Medium.

> **Si Substack est réactivé** : ajouter les lignes suivantes dans la section `gather_entries()` du script Python :
> ```python
> SUBSTACK_BASE = "https://insights.genairadar.co"
> substack_entries = fetch_substack_archive_api(SUBSTACK_BASE, limit=10)
> entries.extend(substack_entries)
> ```

### 2. Contournement du 403 Medium — double stratégie

Le fetch Medium suit désormais une logique en cascade :

**Étape 1 — Fetch direct avec headers navigateur améliorés**
```python
FEED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Referer": "https://medium.com/",
}
```

**Étape 2 — Fallback via proxy RSS-to-JSON** (si step 1 retourne 403)

Deux services de proxy public sont tentés dans l'ordre :
- `https://api.rss2json.com/v1/api.json?rss_url={feed_url}`
- `https://feed2json.org/convert?url={feed_url}`

Ces services proxifient le feed Medium côté serveur, contournant ainsi le blocage IP de GitHub Actions.

### 3. Garde-fou anti-tableau-vide

Dans la fonction `main()`, un check bloquant a été ajouté **avant** toute écriture dans le README :

```python
if not latest:
    print("WARNING: 0 articles fetched — README left unchanged to avoid data loss.")
    return
```

Si 0 articles sont récupérés (quelle qu'en soit la raison), le README est conservé tel quel. Le workflow se termine proprement sans commit, préservant les données existantes.

---

## Résultat

Run #443 (10 mars 2026, 17h05 CET) — **Success en 1m 7s**

```
=== Starting article fetch ===
Trying Medium direct: https://medium.com/feed/@hugomrvt
  Status: 200
  Got 6 entries

=== Final selection: 5 articles ===
1. [2026-03-07] Aligned but unfit: What happens when AI agents get real tools...
2. [2026-01-14] AI as a new territory for brand expression, beyond technology
3. [2026-01-11] When AI devours its own children: The existential crisis of Tailwind CSS
4. [2025-10-09] The Super-App inflection point: How OpenAI is redesigning the digital experience
5. [2025-07-27] The adoption of design systems: beyond code...
README updated with latest posts
```

Le tableau est désormais peuplé et se met à jour automatiquement à chaque publication sur Medium.

---

## Architecture actuelle du workflow

```
recent-posts.yml
│
├── Trigger : cron 0 6,18 * * * (08h et 20h Paris)
│             workflow_dispatch (manuel)
│
├── Source  : Medium RSS feed (@hugomrvt)
│             └── Fetch direct (headers navigateur)
│             └── Fallback proxy rss2json / feed2json si 403
│
├── Guard   : if 0 articles → README inchangé
│
└── Output  : Tableau Markdown injecté entre
              <!-- LATEST_ARTICLES_START --> et <!-- LATEST_ARTICLES_END -->
```

---

## Ajouter Substack comme source future

Quand `insights.genairadar.co` contiendra des articles publiés, réactiver la source en éditant `.github/workflows/recent-posts.yml` :

1. Ajouter en haut du script Python :
```python
SUBSTACK_BASE = "https://insights.genairadar.co"
SUBSTACK_FEED = f"{SUBSTACK_BASE}/feed"
```

2. Dans `gather_entries()`, avant le fetch Medium :
```python
# Substack via JSON API
substack_entries = fetch_substack_archive_api(SUBSTACK_BASE, limit=10)
print(f"Substack API: {len(substack_entries)} entries")
entries.extend(substack_entries)

# Substack RSS fallback
if not substack_entries:
    substack_rss = fetch_feed_entries(SUBSTACK_FEED, limit=10)
    entries.extend(substack_rss)
```

> Note : les fonctions `fetch_substack_archive_api` et `fetch_feed_entries` ont été retirées dans le refactor du 10 mars 2026. Il faudra les réimporter depuis l'historique git si nécessaire (commit `6f9b30d`).
