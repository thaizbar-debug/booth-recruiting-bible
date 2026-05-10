# Thaiz's Recruiting Bible — Booth MBA 2026–2028

My personal recruiting command center. Everything I need to build my LAMP list, run the 6-Point Email + 3v7 system, execute TIARA conversations, track every company and contact, and land a summer internship and full-time offer.

## Structure

```
booth-recruiting-bible/
├── about-me/              # Profile, goals, key stories, visa constraints
├── companies/             # LAMP list + individual company profiles
│   ├── lamp-list.md       # Master 40+ employer table (sort by Alumni > Motivation > Postings)
│   └── _template.md       # Company profile template — copy for each top-6 firm
├── networking/            # CRM: every contact, outreach, and relationship
│   └── tracker.md         # Full networking CRM with BOC taxonomy + 3v7 system
├── applications/          # Application tracker, materials, deadlines
│   └── tracker.md         # Application tracker with case prep log + CPT notes
├── resources/             # Research tools, job boards, Booth resources
│   ├── lamp-tools.md      # How to find companies, contacts, and email addresses
│   ├── company-research.md # Industry intelligence sources
│   ├── job-boards.md      # Job boards by track
│   └── timeline.md        # Phase 0–4 recruiting timeline
├── interview-prep/        # Case prep, STAR stories, track-specific FAQs
├── resumes/               # Resume versions by track
├── daily-briefing/        # Automated daily email: market news + CPG/brand drills
└── .claude/commands/      # AI recruiting skills (slash commands)
    ├── job-search.md      # Find internship opportunities
    ├── outreach.md        # Write 6-Point Emails + manage 3v7 follow-up
    ├── informational.md   # TIARA prep + coffee chat coaching
    ├── coffee-chat.md     # Original coffee chat command
    ├── pre-mba.md         # Pre-Booth skill building
    └── fulltime.md        # Full-time offer strategy
```

## The System (Steve Dalton's Two-Hour Job Search)

### Step 1 — LAMP List
Build a 40+ employer list. Sort by **A (Alumni) → M (Motivation) → P (Postings)**. Your top 6 by alumni access = first outreach wave. See `companies/lamp-list.md`.

### Step 2 — 6-Point Email
Cold outreach under 75 words. About them, not you. Ask for knowledge, not a job. LinkedIn link, not resume. See `/outreach` command.

### Step 3 — 3v7 Routine
After every outreach: set reminders at +3 business days (try different contact at same firm) and +7 business days (one follow-up via different channel). Max 2 attempts per person. See `networking/tracker.md`.

### Step 4 — TIARA
Informational meeting framework: **Trends → Insights → Advice → [Pivot] → Resources → Assignments**. Never ask for a referral during the meeting — that comes 1 week later. See `/informational` command.

### Step 5 — Ben Franklin Check-Ins
After every TIARA: monthly updates to Boosters sharing something useful. Never asking for anything. Builds real relationships. See `networking/tracker.md`.

## Slash Commands

| Command | Purpose |
|---|---|
| `/job-search` | Find summer internship opportunities (CPT, summer 2027) |
| `/outreach` | Write 6-Point Emails + manage 3v7 follow-up calendar |
| `/informational` | Prep for + debrief coffee chats using TIARA |
| `/coffee-chat` | Craft cold outreach + prepare for coffee chats (original) |
| `/pre-mba` | Build experience and skills before Booth starts |
| `/fulltime` | Full-time offer strategy (Year 2 recruiting) |

## Daily Briefing

An automated script (`daily-briefing/briefing.py`) runs every morning at 8 AM Chicago time via GitHub Actions — US consumer pulse, brand moves, CPG/retail news, vocabulary drills, brand management concepts, consumer insights methodology, P&L literacy. Setup: copy `daily-briefing/.env.example` → `.env`, fill in credentials, push to GitHub. Add `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` as Actions secrets.

## Recruiting Timeline

| Phase | Window | Priority |
|---|---|---|
| Pre-MBA prep | Now → Sept 2026 | Build LAMP list, LinkedIn, skills, Chicago network |
| Year 1 networking | Sept → Nov 2026 | TIARA conversations, club leadership, firm events |
| Summer recruiting | Oct 2026 → Feb 2027 | 6-Point Emails, applications, interviews, offers |
| Summer internship | June → Aug 2027 | Perform → convert to FT offer |
| Full-time recruiting | Sept → Dec 2027 | FT applications, interviews, signed offer |

## My Story in One Paragraph

I'm a Peruvian product manager turned strategic consultant, with experience at Peru's largest bank (BCP), its second highest-funded startup (Favo), and its most robust consulting firm. I have a double degree in Engineering and Business. I build things that improve people's lives — from gamified savings apps for low-income communities to cross-functional team structures that break silos. I'm going to Booth to pivot into brand management and marketing strategy, starting with a consulting internship that lets me specialize in CPG and retail.
