# Thaiz's Recruiting Bible — Booth MBA 2026–2028

My personal recruiting command center. Everything I need to land a summer internship, build my network, make the most of the time before Booth starts, and secure a full-time offer.

## Structure

```
booth-recruiting-bible/
├── about-me/           # Profile, goals, key stories, visa constraints
├── target-companies/   # Research on consulting firms, CPG brands, tech-marketing
├── networking/         # Outreach templates, coffee chat tracker
├── applications/       # Application tracker, cover letter templates
├── resources/          # Timeline, Booth-specific resources
├── daily-briefing/     # Automated daily email: market news + CPG/brand skill drills
└── .claude/commands/   # AI recruiting skills (slash commands)
```

## Daily Briefing

An automated script (`daily-briefing/briefing.py`) runs every morning at 8 AM Chicago time via GitHub Actions. It scrapes 28+ sources and emails a structured briefing with 9 sections:

- **Sections 1–5:** US consumer pulse, brand moves, CPG/retail news, tech trends, interview-ready facts
- **Section 6:** CPG Vocabulary Drill (one new term daily — trade spend, planogram, household penetration, etc.)
- **Section 7:** Brand Management Concept of the Day (brand equity, penetration vs. frequency, Byron Sharp, etc.)
- **Section 8:** Consumer Insights Methodology (A&U studies, panel data, conjoint analysis, etc.)
- **Section 9:** P&L Literacy Drill (gross margin, A&P budget, trade spend ROI, etc.)

Setup: copy `daily-briefing/.env.example` → `daily-briefing/.env`, fill in credentials, push to GitHub. Add `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` as GitHub Actions secrets.

## The Four Skills

| Command | Purpose |
|---|---|
| `/job-search` | Find summer internship opportunities (CPT, summer 2027) |
| `/coffee-chat` | Craft cold outreach + prepare for coffee chats |
| `/pre-mba` | Ideas to build experience before Booth starts |
| `/fulltime` | Full-time offer strategy (Year 2 recruiting) |

## Recruiting Timeline

| Phase | Window | Priority |
|---|---|---|
| Pre-MBA prep | Now → Sept 2026 | Build skills, network in Chicago, optimize LinkedIn |
| Year 1 networking | Sept → Nov 2026 | Coffee chats, company events, club leadership |
| Summer recruiting | Oct 2026 → Feb 2027 | Applications, interviews, offers |
| Summer internship | June → Aug 2027 | Perform, convert to FT offer |
| Full-time recruiting | Sept → Dec 2027 | FT applications and interviews |

## My Story in One Paragraph

I'm a Peruvian product manager turned strategic consultant, with experience at Peru's largest bank (BCP), its second highest-funded startup (Favo), and its most robust consulting firm. I have a double degree in Engineering and Business. I build things that improve people's lives — from gamified savings apps for low-income communities to cross-functional team structures that break silos. I'm going to Booth to pivot into brand management and marketing strategy, starting with a consulting internship that lets me specialize in CPG and retail.
