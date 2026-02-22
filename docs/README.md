# AI agents end-to-end

- AI agents leverage LLMs either in your own [AI Landing Zones](#ai-landing-zones) or as part of a managed solution.
- They need [Runtime](#runtime-framework) to operate and must be [hosted](#hosting) somewhere. 
- Agents need to access various [tools](#tools) to take actions and [knowledge](#knowledge-with-microsoft-iq) for grounding.
- Users interact with agents from different experiences using [channels](#channels). 
- Agents typically need to work with [identity](#identity-authentication-and-authorization) to support user-delegated permissions, but they also have their own identity for autonomous tasks (to access tools, knowledge, or other agents).
- All of this should be governed by a control plane to provide [administration](#agents-control-plane), [observability](#observability), catalog, onboarding, security, and more.
- Security must understand the current agent landscape, posture, conditional access policies for agents, [responsible AI](#responsible-ai-and-agent-security), and [data security](#data-security).
- Successful development and continuous improvement of agents require strong [evaluations](#evals) and lifecycle management with [CI/CD](#cicd).

See also [User Stories](./UserStories.md) for more context on needs and requirements for AI agents in the enterprise.

## AI Landing Zones
<details>
<summary>
Provide governance and management of Large Language Models (LLMs) and other AI resources, including access control for different teams and use-cases, cost management, security policies, compliance, or model monitoring.
</summary> 

---

Models can be accessed via APIs from different runtimes and hosting options including Foundry Agents and Copilot Studio, but can also be provided as part of the platform.

A large-scale enterprise AI landing zone uses **Azure API Management** as an AI gateway to manage access to LLMs. This includes clever routing and capacity policies (priority-based access to models and provisioned throughput, plus capacity and quota management), access control, and auditing capabilities (you can, for example, record interactions without depending on developers to use OpenTelemetry properly), inline security enforcement, and so on. This can also be used to provide a data plane proxy for MCP tools.

**Foundry** provides the concept of resources and projects, plus a UI experience such as playgrounds and an agent builder. Support for bring-your-own APIM with full UI support is planned.

**Foundry** provides a developer-oriented experience with observability, evals, a model catalog, an agents catalog, and a tools catalog for ease of discovery.

</details>

## Runtime (framework)
<details>
<summary>
An LLM is stateless, so you need a runtime for your agent so it can maintain state, manage context, and handle interactions over time.
</summary>

---

### Responses API
can be considered a lightweight runtime, but we will focus on more advanced ones.

### Copilot Studio 
is a no-code platform (leverages Foundry runtime under the covers).

### Foundry Agent Service
is a developer-oriented platform ranging from declarative agents and workflows all the way to hosting other runtimes.

### Microsoft Agent Framework 
is an open-source framework, very strong in **Python** and **C#**, sponsored by Microsoft to build agents, multi-agent systems, and orchestration workflows that can be hosted anywhere.

### LangGraph
is another open-source framework, strong in **Python** (less so in **JavaScript**), modern and low-level, but it can leverage some higher-level constructs from LangChain. It has a community-driven clone for Java.

</details>

## Hosting
<details>
<summary>
An agent runtime is code that must run somewhere. Hosting can be provided as part of the platform (e.g., Copilot Studio, Foundry Agent Service) or you can self-host any runtime and connect it to Microsoft 365 with the **Microsoft 365 Agents SDK**.
</summary>

---

### Hosted options
**Copilot Studio** hosts only Copilot Studio agents, but it can interact with other agents and tools outside of it.

**Foundry Agent Service** supports multiple runtimes, such as **declarative Foundry agents**, hosting of **Microsoft Agent Framework** or **LangGraph**, and others via SDK integration and a Docker image.

### Self-hosted options
can run anywhere, e.g., **Azure Container Apps** or **Azure Kubernetes Service**, and can use the **Microsoft 365 Agents SDK** to integrate into governance, security, and Microsoft experiences.

</details>

## Tools
<details>
<summary>
Agents are more useful when they can access various tools (APIs, data sources, other agents, and so on). A common protocol for agent-to-tool interaction is Model Context Protocol (MCP). Access control and authorization should be based on authn/authz principles via Entra ID identities and scopes, ideally managed as part of Agent 365.
</summary>

---

**Authentication and authorization** for tools is critical and should be based on Entra ID identities and scopes. Enforcing zero trust end-to-end by having MCP publish various scopes in Entra ID, and having users (via OBO) and agents (via their Agent ID) request access to the proper scope, is a good practice. Granular authorization is also important: an MCP server can parse tokens for claims such as roles or security groups and enforce different authorization policies based on that. Note that MCP is often built on top of existing APIs, so authn/authz should be implemented at the API level with MCP acting as a facade. In situations where backends are not ready for this, you can use **Azure API Management** to enforce it.

**Data plane policies** are also important to protect backend systems from overuse (implement limits and quotas), implement clever routing and version control (including A/B testing or canary releases), enforce auditing and logging of interactions, or apply network restrictions. These can be handled by **Azure API Management**, including republishing an API as MCP or providing a data plane proxy for MCP.

**Catalog** of tools (endpoints, capabilities) is available as part of **Agent 365**, which works as a **tool gateway** for agents and is visible in Microsoft agent development tools such as **Copilot Studio**. As part of this, Microsoft provides access to built-in MCP tools such as Outlook Email, Teams, SharePoint and OneDrive, Outlook Calendar, Microsoft Dataverse, and others.

Another, more extensible and universal catalog option is **Azure API Center**, which can automatically import MCPs from **Azure API Management**, supports a web-based catalog with RBAC capabilities, and can be accessed programmatically so a list of private MCPs is available in **Foundry Agent Service** or in the **GitHub Copilot** private MCP list for developers.

</details>

## Knowledge with Microsoft IQ
<details>
<summary>
Agents need knowledge to be able to do useful tasks. Although various individual data sources can be connected directly via tools, this leads to poor results, high latency, and inconsistencies. Microsoft recommends providing curated knowledge sources with built-in agentic search capabilities prepared and tuned by teams that understand the data. This is provided by Microsoft IQ: WorkIQ, Foundry IQ, and Fabric IQ.
</summary>

---

All IQs provide a simple interface (based on MCP) for agents to access complex knowledge without needing to understand the complexity of underlying data sources and retrieval techniques.

### WorkIQ
brings knowledge about people and work - emails, calendar, chats, meetings and more.

### Foundry IQ
brings institutional knowledge about your organization - documents, wikis, databases, policies and more.

You can build processing pipelines that ingest knowledge from various sources, including poorly structured data such as PDFs and other documents, and build indexes in **AI Search**. Agents simply use Foundry IQ, which then implements agentic search such as query rewriting and decomposition into more parallel queries, various retrieval techniques such as semantic search (vectors) and keyword search (with clever tricks such as synonym expansion, stemming, lemmatization, BM25), and semantic ranking and filtering of results.

### Fabric IQ
brings semantic knowledge about your business - customers, products, inventory, transactions and more.
Data teams are responsible for managing data and adding a semantic layer on top, which allows agents to understand business data. Fabric then provides, for example, a data agent for different uses so your AI agents can simply ask questions about business data.

</details>

## Channels
<details>
<summary>
Agents are increasingly accessed using various channels such as custom web interface, Microsoft Teams, Microsoft 365 Copilot, Slack, SMS, Azure Communication Services and others. This requires much more than messages - it must support various events (reasoning events about what the agent is doing now), typing, commands, multi-modality, or even declarative UI extensions on top such as Adaptive Cards.
</summary>

---

Channels should require strong authentication and authorization tunnels, provide a user on-behalf-of flow, and some might need to be accessed via the public internet (Copilot, Teams), so they require a proxy (relay) to expose agents hosted in internal networks securely.

In Microsoft's story, this is handled with **Activity Protocol** and the **Microsoft 365 Agents SDK** (for any agent) and automatically provided by **Copilot Studio** and **Foundry Agent Service** (supported for all agent types - Foundry Agent, hosted Microsoft Agent Framework, hosted LangGraph, and others).

</details>

## Identity, authentication and authorization
<details>
<summary>
Agents published to Microsoft 365 have identities assigned automatically and you can manage permissions in Agent 365. Nevertheless, the underlying identity capabilities of Microsoft Entra can be used for other agents as well, although it requires manual configuration of permissions and access policies. Agents work with user-delegated permissions, but they also have their own identity for autonomous tasks (to access tools, knowledge, or other agents).
</summary>

---

Agents do support an **on-behalf-of** (OBO) user authentication flow (delegated permissions and consent), where **Azure Bot Service** together with the **Microsoft 365 Agents SDK** or **Agent 365 SDK** (which comes with a managed bot service) are responsible for token exchange and management of the OBO flow. The agent builder can request specific delegated scopes and the SDK delivers access tokens for direct use with tools and services such as custom MCP servers, Microsoft Graph (e.g., for user information), or Microsoft MCP tools.

Agent identity (similar to app registration client credentials, but curated for agents) is used for **background agents** without user interaction and for **agent-to-agent** scenarios. In the future, there will also be an "agent user ID" which is a user-like identity for agents that act as a full digital employee with their own Outlook, Teams, notifications, and so on.

</details>

## Agents control plane
<details>
<summary>
Agents can be published in Microsoft 365 with their icon, description, permissions required and other metadata. This allows users to discover them in Microsoft 365 and use them in various channels such as Microsoft 365 Copilot, Microsoft Teams and others. It also allows administrators to manage them, control access to them, and monitor their usage. Agent 365 then provides a control plane over all agents, including auditing, observability, and access control, all the way to the concept of a digital worker.
</summary>

---

All agents built in **Copilot Studio** and **Foundry Agent Service** (using any agent - Foundry Agent, hosted Microsoft Agent Framework, hosted LangGraph and others) can be easily published to the Microsoft 365 ecosystem. Agents built with any framework and self-hosted can be published to Microsoft 365 when instrumented with the **Microsoft 365 Agents SDK**.

While the **Microsoft 365 Agents SDK** provides discoverability for any agent, the **Agent 365 SDK** allows any agent to integrate into identity, auditing, observability, security, and tool access in **Agent 365**, as well as forward-looking capabilities such as a digital worker experience with an agent user ID with access to its own email, Teams or Word comments, receiving notifications about new messages or meetings and mentions, and so on.

**Agent 365** has the concept of **blueprints**, which are basically agent templates with predefined capabilities, permissions, and tool access requirements. Users can request **instantiation** of such an agent for their use (hire an agent), which creates a specific instance with its own Agent ID in Entra ID and permissions consented or fine-tuned by the user.

Why are **coding agents** different yet important to mention here? Expect much less off-the-shelf software and much more custom code written by agents. Coding agents are typically not published to the general user population (yet), but local tools such as GitHub Copilot CLI, Claude Code, and CODEX are often used by developers, as well as cloud-based agents in GitHub. **GitHub Agents HQ** is a control plane for coding agents (GitHub and third-party), providing governance, management, monitoring, and shared memory. Note agents can now work as part of deployment pipelines with **GitHub Agentic Workflows**.

</details>

## Observability
<details>
<summary>
Agents are non-deterministic systems by nature, therefore observability is even more important than with apps. Developers need to understand how their agents are performing, identify issues and bottlenecks, and continuously improve them. Observability includes traces of conversations so developers can understand what the agent is doing, what tools are called, and how agents take turns in a multi-agent system, including dimensions such as time and latency, user ID, role, group, tenant, and so on.
</summary>

---

Industry is standardizing on OpenTelemetry for agents with GenAI semantic conventions.

**Copilot Studio** and **Foundry Agent Service** both provide built-in observability capabilities, including logging, metrics, and tracing, to monitor agent performance and behavior. Both support direct access to **Application Insights** so customers can query raw data and build dashboards in Azure Monitor, Grafana, or Power BI.

At this point Copilot Studio and Foundry Agents do not support custom OpenTelemetry endpoints for different observability systems, but you can use it with hosted agents in Foundry (Microsoft Agent Framework, LangGraph, etc.) and with self-hosted agents (e.g., with agents connected via the Microsoft 365 Agents SDK). With those, you can even implement an OpenTelemetry Collector to receive a single stream of telemetry and distribute it to various monitoring systems such as Foundry, LangFuse, or Grafana Tempo.

**Agent 365** platform works as an OpenTelemetry collector and provides observability to published agents or custom agents instrumented with the **Agent 365 SDK**. Data collected is leveraged by multiple Microsoft products - Agent 365 for providing basic monitoring, Defender for AI for security insights, Purview for data security - and can also be forwarded to Microsoft Sentinel. As of February 2026, customers cannot yet access raw telemetry data directly.

Strategy (not available today) is instrument once, observe everywhere - developers in Foundry, admins in Agent 365, operations in Azure Monitor.

Vision for 2026 is intelligent observability - agents look after other agents.

</details>

## Data security
<details>
<summary>
AI agents should be governed by the same data protection and compliance standards as other enterprise systems. This is provided by Microsoft Purview across Microsoft 365, Azure, and Agent 365.
</summary>

---

AI agents inherit **Microsoft Purview** protections for data classification, sensitivity labels, DLP, and auditing. Agents respect user and agent identity-based access controls, and are subject to the same encryption and compliance policies as users. Purview integrates with Microsoft 365, Azure, and Agent 365 to enforce zero-trust, prevent oversharing, and ensure full auditability. Agent-specific risk indicators and observability are available via Agent 365 and Purview dashboards.

</details>

## Responsible AI and agent security
<details>
<summary>
AI agents should have guardrails to prevent harmful or unintended behavior as well as mechanisms to detect attempts at jailbreaking and other security threats.
</summary>

---

Microsoft **AI Guiderails** provide **inline** protection against harmful content, PII oversharing, or jailbreaking risks. Because they sit in the flow of agent-to-LLM interactions, they can block or annotate risks in real time.

Microsoft **Defender for AI** provides **post-hoc** detection capabilities and therefore can detect more complex attack patterns. It also brings posture management capabilities with risk indicators and recommendations for mitigation. Defender for AI is reporting to overall **Defender for Cloud** and **Microsoft Sentinel** for correlation with other security signals and overall SOC monitoring.

</details>

## Evals
<details>
<summary>
Evaluations are a critical success factor for building high-quality agents because what cannot be measured cannot be improved.
</summary>

---

### Copilot Studio
has built-in eval capabilities.

### Foundry Agent Service
comes with rich built-in evaluations and red teaming capabilities, including offline and batch evals, as well as continuous evaluations and CI/CD integration.

Moreover, all Foundry agent types do support API access and can therefore be evaluated with any eval framework such as DeepEval or LangFuse.

</details>



## CI/CD
<details>
<summary>
Agents need to be continuously improved and updated, and for that we need a process to test and implement changes.
</summary>

---

**Copilot Studio** supports CI/CD capabilities via Power Platform ALM

**Foundry Agent Service** fully supports any CI/CD for all types of agents - even declarative agents can be managed effectively via SDK.

</details>




