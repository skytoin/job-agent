# Job Apply Skill

Use this skill when working on browser agent task prompts, debugging form-filling issues, or tuning the application pipeline.

## Common ATS Systems

| ATS | URL Pattern | Quirks |
|-----|-------------|--------|
| Greenhouse | `boards.greenhouse.io` | Clean forms, usually 1 page |
| Lever | `*.lever.co` | May require account creation |
| Workday | `*.myworkdayjobs.com` | Multi-page (3-5), slow, use max_steps=50 |
| SmartRecruiters | `*.smartrecruiters.com` | Usually straightforward |
| Ashby | `*.ashbyhq.com` | Modern, clean forms |
| iCIMS | `*.icims.com` | Older UI, may need explicit waits |

## Debugging Form-Fill Failures

1. Check `output/logs/agent-N/` for the conversation trace
2. Common failures:
   - **File upload**: Ensure resume_path is absolute, not relative
   - **Dropdown not selecting**: Add explicit "select [value] from the [field name] dropdown" to prompt
   - **Multi-page stuck**: Agent hit max_steps, increase to 50
   - **Login loop**: Pre-authenticate in the browser profile manually
3. Re-run single job: `uv run python run.py --job-index N`

## Task Prompt Patterns

For custom questions like "Why do you want to work here?":
```
For open-ended questions about motivation or interest, write 2-3 sentences
connecting {profile.current_title} experience with the specific requirements
mentioned in the job posting. Be specific, not generic.
```

For salary fields with specific formats:
```
If salary field requires a single number, enter: {profile.salary_expectation.split('-')[0]}
If it's a range with two fields, enter min: X and max: Y
```
