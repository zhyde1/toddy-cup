# Toddy Time Cup

Automatic standings and scoreboards for the 9-category in-season tournament. A GitHub job pulls
exact stats from ESPN every 30 minutes, scores the cup, and republishes the page.

Page address once set up: https://zhyde1.github.io/toddy-cup/

## One-time setup

1. **Create the repository.** On github.com click New repository. Name it `toddy-cup`, choose Public, click Create.
2. **Upload the files.** On the new repo page click "uploading an existing file". Drag in `docs` (the folder),
   `config.json`, `update.py`, `requirements.txt`, and `README.md`. Click Commit changes.
3. **Add the workflow.** Click Add file, then Create new file. In the name box type
   `.github/workflows/update.yml` (typing the slashes makes the folders). Paste in the contents of
   the `update.yml` file, then Commit changes.
4. **Add your ESPN cookies as secrets.** Settings, then Secrets and variables, then Actions, then New repository secret.
   Create two secrets with these exact names: `ESPN_S2` and `SWID`. Paste each value with nothing added
   (keep the curly braces on SWID).
5. **Turn on Pages.** Settings, then Pages. Under Source choose **GitHub Actions**.
6. **Run it once.** Open the Actions tab, click "Update cup standings", then Run workflow. After about a minute the page is live.

After that it refreshes itself every 30 minutes.

## Keeping it running

- **Cookies expire.** If the job starts failing (GitHub emails you), get fresh `espn_s2` and `SWID` values
  the same way as before and replace the two secrets. The page keeps showing the last good numbers meanwhile.
- **Change the schedule or conferences:** edit `config.json` on GitHub (pencil icon). Each team is matched by its
  ESPN `team_id`, so team renames in ESPN need no change.
- **Never paste cookies into any file in this repo.** The repo is public. Secrets are the only safe place.

## How scoring works

- Each team's weekly totals are its starters' stats summed day by day, Monday to Sunday (bench and IR excluded).
- Nine categories: PTS, AST, REB, 3PM, STL, BLK, FG%, FT%, and TO (fewer is better).
- Five or more categories wins the matchup. Standings rank by wins, then head-to-head among tied teams, then total categories won.
- Top 2 per conference play the semifinal (1st vs 2nd in the same conference). The winners play the final.

## Test without ESPN

`python update.py --mock 6 --out /tmp/data.json` builds fake data as if week 6 were in progress.
