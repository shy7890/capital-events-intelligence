# Third-party notices and provenance checklist

This file records the current review boundary for the repository. It is not a substitute for checking the exact files and upstream terms before a public release.

## Dependencies and referenced projects

| Project or service | Role in this repository | License / status |
| --- | --- | --- |
| FastAPI, Uvicorn, Pydantic Settings, Pandas, pytest | Python dependencies | Review the installed package licenses; not bundled as source |
| Akshare | Market, announcement, shareholder-count, and ranking adapters | MIT dependency; upstream access and data terms still apply |
| Baostock | Daily market-data adapter | MIT dependency; upstream access and data terms still apply |
| Apache ECharts, Ant Design, React, Vite, TypeScript | Frontend dependencies | Review package-lock license metadata; not relicensed by this repository |
| CNINFO and exchange/public web services | Disclosure and public-data sources | Terms and redistribution rights remain with each provider |
| iFinD MCP (optional) | Optional authorized financial-data path | Private/provider-controlled; credentials and production endpoint are excluded |

## Code provenance items to review before publishing

- The throttled Eastmoney request pattern and selected data mappings were developed with reference to external open-source projects. Confirm attribution, retained notices, and any modifications before publishing a new public repository.
- The announcement classification ontology should be treated as project-specific until the provenance and license of any referenced rule set are confirmed.
- Do not publish local databases, historical screening workbooks, issuer research folders, screenshots, deployment runbooks, or provider credentials as part of the public package.

## Scope of the repository license

`LICENSE` applies to original project code only. It does not grant permission to copy upstream data, websites, trademarks, commercial APIs, or third-party source code whose license or provenance has not been separately confirmed.
