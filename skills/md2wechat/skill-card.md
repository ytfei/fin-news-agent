## Description: <br>
Convert Markdown to WeChat Official Account HTML and guide agents through article formatting, previews, draft upload, image generation, title suggestions, and discovery of supported providers, themes, prompts, and layout modules. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[geekjourneyx](https://clawhub.ai/user/geekjourneyx) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
Developers, creators, and publishing teams use this skill to route Markdown articles through the md2wechat CLI for WeChat formatting, preview, draft creation, image-post workflows, and related content-generation tasks. It helps agents choose the right CLI workflow while preserving explicit approval gates for uploads, drafts, publishing, file writes, and image generation. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Upload, draft, publishing, and direct image-generation workflows can use account or provider credentials and create external side effects. <br>
Mitigation: Only approve upload, draft, publishing, or image-generation actions when the user explicitly asks for them and is comfortable with the relevant configured credentials being used. <br>
Risk: Article advice, title suggestions, writer-style drafting, and AI trace removal can produce recommendations or generated content that may be inaccurate or unsuitable for publication. <br>
Mitigation: Treat these outputs as recommendations, review the content before use, and keep inspect readiness checks as the publish gate. <br>
Risk: Generated Markdown or formatted article artifacts could alter source content if saved in place. <br>
Mitigation: Keep the source Markdown read-only by default, use temporary formatted artifacts, and save next to the source only with explicit user confirmation. <br>


## Reference(s): <br>
- [ClawHub skill page](https://clawhub.ai/geekjourneyx/skills/md2wechat) <br>


## Skill Output: <br>
**Output Type(s):** [Text, Markdown, Shell commands, Configuration, Guidance] <br>
**Output Format:** [Markdown guidance with inline shell commands and JSON-reading instructions] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [May create temporary Markdown preview or formatted article artifacts when the user requests conversion workflows; publishing and image-generation side effects require explicit user approval.] <br>

## Skill Version(s): <br>
3.1.0 (source: server release evidence) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>
