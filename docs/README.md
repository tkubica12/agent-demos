# AI agents end-to-end

- AI agents do leverage LLMs either in your own [AI Landing Zones](#ai-landing-zones) or included in managed solution. 
- They need [Runtime](#runtime-framework) to operate and must be [hosted](#hosting) somewhere. 
- Agent need to access various [tools](#tools) to do actions and [knowledge](#knowledge-with-microsoft-iq) for grounding. 
- Users interact with agents from different experiences using [channels](#channels). 
- Agent typicaly need to work with [identity](#identity-authentication-and-authorization) to support user delegated permissions, but also have its own identity for autonomous tasks in order to access tools, knowledge or other agents. 
- This all should be governed by control plane to provide [administration](#agents-control-plane), [observability](#observability), catalog and more. 
- Security must understand curent agent landscape, posture, conditional access policies for agents, [responsible AI](#responsible-ai-and-agent-security) and [data security](#data-security). 
- Successful development and continuous improvement of agents require strong [evaluations](#evals) and lifecycle management with [CI/CD](#cicd).

## AI Landing Zones
<details>
<summary>
Provide governance and management of Large Language Models (LLMs) and other AI resources, including access control for different teams and use-cases, cost management, security policies, compliance or model monitoring.
</summary> 

---

Models can be accessed via APIs from different runtimes and hosting options including Foundry Agents and Copilot Studio, but can also be provided as part of the platform.

Large-scale enterprise AI landing zone uses **Azure API Management** as AI Gateway to manage access to LLMs. This includes clever routing and capacity policies (priority-based access to models and privisioned throughput, capacity and quota management), access control, auditing capabilities (you can for example record interactions without dependency on developers use OpenTelemetry properly), inline security enforcement and so on. This can also be used to provide data plane proxy for MCP tools (see [Tools](#tools) section) to agents.

**Foundry** provides concept of resources and project and UI experience such as playgrounds and agent builder. Support for bring-your-own APIM with full UI support is planned. 

**Foundry** provides developer oriented experience with observability, evals, model catalog, agents catalog, tools catalog for easy of discovery.

</details>

## Runtime (framework)
<details>
<summary>
LLM is stateless therefore you need some runtime for your agent so it can maintain state, manage context, and handle interactions over time.
</summary>

---

### Responses API
can be considered lightweight runtime, but we will focus on more advanced ones.

### Copilot Studio 
is no-code platform (leverages Foundry runtime under covers).

### Foundry Agent Service
is developer oriented platform ranging from declarative agents and workflows all the way to hosting hosting other runtimes.

### Microsoft Agent Framework 
is open source framework very strong in **Python** and **C#** sponsored by Microsoft to build both agents, multi-agent systems and orchestration workflows that can be hosted anywhere.

### LangGraph
is another open source framework, strong in **Python** (less so in **Javascript**), modern and low-level, but can leverage some higher-level constructs from LangChain. It has community driven clone for Java.

</details>

## Hosting
<details>
<summary>
Agent runtime is code that must run somewhere. Hosting can be provided as part of the platform (eg. Copilot Studio, Foundry Agent Service) or you can self-host any runtime and connect it to Microsoft 365 with **Microsoft 365 Agents SDK**.
</summary>

---

### Hosted options
**Copilot Studio** is hosting only Copilot Studio agents, but can interact with other agents and tools outside of it

**Foundry Agent Service** supports multiple runtimes such as **declarative Foundry agents**, hosting of **Microsoft Agent Framework** or **LangGraph** and others via SDK integration and Docker image

### Self-hosted options
can run anywhere, eg. **Azure Container Apps** or **Azure Kubernetes Service**, and can use **Microsoft 365 Agents SDK** to integrate into governance, security and integration Microsoft experiences.

</details>

## Tools
<details>
<summary>
Agents are more useful when they can access various tools - APIs, data sources, other agents and so on. Common protocol for agent-to-tool interaction is Model Context Protocol (MCP). Access control and authorization should be based on authn/authz principles via Entra ID identities and scopes, ideally managed as part of Agent 365.
</summary>

---

**Authentication and authorization** for tools is critical and should be based on Entra ID identities and scopes. Enforcing zero trust end-to-end by MCP publishing various scopes in Entra ID and have user (via OBO) and agents (via their Agent ID) request access to proper scope is good practice. Also granular authorization is important with MCP server parsing tokens looking for claims such as role or security groups and enforcing different authorization policies based on that. Note that MCP is often built on top of existing APIs so authn/authz should be implemented on API level with MCP being just a facade. For situations when backends are not ready for this you can use **Azure API Management** to enforce this.

**Data plane policies** are also important to protect backend systems from overuse (implement limits and quotas), implement clever routing and version control (including A/B testing or canary releases), enforced auditing and logging of interactions or network restrictions and be handled by **Azure API Management** inluding republishing API as MCP or providing data plane proxy for MCP.

**Catalog** of tools (endpoints, capabilities) is available as part of **Agent 365** which works as **tool gateway** for agents and is visible in Microsoft agent development tools such as **Copilot Studio**. As part of this Microsoft provides access to built-in MCP tools such as Outlook Email, Teams, Sharepoint and OneDrive, Outlook Calendar, Microsoft Dataverse and others.

Another, more extensible and universal catalog option is **Azure API Center** that can automatically import MCPs from **Azure API Management**, supports web-based catalog with RBAC capabilities and can be accessed programatically so list of private MCPs is available in **Foundry Agent Service** or in **GitHub Copilot** private MCP list for developers.

</details>

## Knowledge with Microsoft IQ
<details>
<summary>
Agents need knowledge to be able to do useful tasks. Although various individual data sources can be connected directly via tools, this leads to poor results, high latency and inconsistencies. Microsoft recommends providing curated knowledge sources with build-in agentic search capabilities prepared and tuned by teams that understand the data. This is provided by Microsoft IQ- WorkIQ, Foundry IQ and Fabric IQ.
</summary>

---

All IQs provide simple interface (based on MCP) for agents to access complex knowledge without need to understand complexity of underlying data sources and retrieval techniques. 

### WorkIQ
brings knowledge about people and work - emails, calendar, chats, meetings and more.

### Foundry IQ
brings institutional knowledge about your organization - documents, wikis, databases, policies and more.

You can build processing pipelines that ingest knowledge from various sources including poorly structured data such as PDFs and other documents and build indexes in **AI Search**. Agents are simply using Foundry IQ which than implements agentic search such as query rewriting and decomposition into more paralel queries, various retrieval techniques such as semantic search (vectors), keyword search (with clever tricks such as synonym expansion, stemming, lemmatization, BM25) and semantic ranking and filtering of results.

### Fabric IQ
brings semantic knowledge about your business - customers, products, inventory, transactions and more.

Data teams are responsible for managing data and adding semantic layer on top which allows agents to understand business data. Fabric then provides for example data agent for different uses so your AI agents can simply ask questions about business data.

</details>

## Channels
<details>
<summary>
Agents are more and more accessed using various channels such as custom web interface, Microsoft Teams, Microsoft 365 Copilot, Slack, SMS, Azure Communication Services and others. This requires much more then messages - it must support various events (resoning events about what agent is doing now), typing, commands, multi-modality or even declarative UI extensions on top such as Adaptive Cards. 
</summary>

---

Channels should require strong authentication and authorization tunnels, provide user on-behalf-of flow and some might need to be access via public Internet (Copilot, Teams) so require proxy (relay) to expose agents hosted in internal networks securely.

This is in Microsoft story handled with **Activity Protocol** and **[Microsoft 365 Agents SDK]** (for any agent) and automatically provided by **Copilot Studio** and **Foundry Agent Service** (supported for all agent types - Foundry Agent, hosted Microsoft Agent Framework, hosted LangGraph and others).

</details>

## Identity, authentication and authorization
<details>
<summary>
Agents published into Microsoft 365 have identities assigned automatically and you can manage permissions in Agent 365. Nevertheless, underlying identity capabilities of Microsoft Entra can be used for any other agents also, although it requires manual configuration of permissions and access policies. Agents work with user delegated permissions, but also have their own identity for autonomous tasks in order to access tools, knowledge or other agents.
</summary>

---

Agents do support **on-behalf-of** (OBO) user authentication flow (delegated permissions and consent) where **Azure Bot Service** together with **Microsoft 365 Agents SDK** or **Agent 365 SDK** (which comes with banaged bot service) are responsible for token exchange and management of OBO flow. Agent builder can request specific delegated scopes and SDK delivers access tokens for direct use with tools and services such as custom MCP servers, Microsoft Graph (eg. for user information) or Microsoft MCP tools.

Agent identity (similar to app registration client credentials, but curated for agents) is used for **background agents** without user interaction and for **agent-to-agent** scenarios. In future there will also be "agent user ID" which is user-like identity for agents that act as full digital employee with their own outlook, Teams, notifications and so on. 

</details>

## Agents control plane
<details>
<summary>
Agents can be published in Microsoft 365 with their icon, description, permissions required and other metadata. This allows users to discover them in Microsoft 365 and use them in various channels such as Microsoft 365 Copilot, Microsoft Teams and others. It also allows administrators to manage them, control access to them and monitor their usage. Agent 365 then provides control plane over all agents including auditing, observability, access control all the way to concept of digital worker.
</summary>

---

All agents built in **Copilot Studio** and **Foundry Agent Service** (using any agent - Foundry Agent, hosted Microsoft Agent Framework, hosted LangGraph and others) can be easily published to Microsoft 365 ecosystem. Agents built with any framework and self-hosted can be published to Microsoft 365 when instrumented with **[Microsoft 365 Agents SDK]**.

While **[Microsoft 365 Agents SDK]** provides dicoverability for any agent, **Agent 365 SDK** allows any agent to integrate into identity, auditing, observability, security and tool access in **Agent 365** as well as forward-looking capabilities such as digital worker experience with agent user ID with access to its own email, Teams or Word comments, receiving notifications about new messages or meetings and mentions and so on.

**Agent 365** has concept of **blueprints** which are basicaly agent templates with predefined capabilities, permissions and tool access requirements. User can request **instantiation** of such agent for their use (hire an agent) which create specific instance with its own Agent ID in Entra ID and permissions consented or fine-tuned by user. 

</details>

## Observability
<details>
<summary>
Developers need to understand how their agents are performing, identify issues and bottlenecks, and continuously improve them. Observability includes traces of conversations so developer can understand what is agent doing, what tools are called, how agents turn in multi-agent system including dimensions such as time and latency, user ID, role, group, tenant, and so on. 
</summary>

**Copilot Studio** and **Foundry Agent Service** provide built-in observability capabilities, including logging, metrics, and tracing, to monitor agent performance and behavior. 

**Foundry Agent Service** goes one level deeper with access to raw traces in **Application Insights** so you can build your own views and dashboards. 

At this point Copilot Studio and Foundry Agents do not support custom OpenTelemetry endpoints for different observability systems, but you can use it with hosted agents in Foundry (Microsoft Agent Framework, LangGraph etc.) and with self-hosted agents (eg. with agents connected via Microsoft 365 Agents SDK).

**Agent 365** platform works as OpenTelemetry collector and provides observability to published agents or custom agents instrumented with **Agent 365 SDK**. At this point this is more high-level (agent usage metrics).

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
AI agents should have guiderails to prevent harmful or unintended behavior as well as mechanisms to detect stop attempts to jailbreaking and other security threats.  
</summary>

---

Microsoft **AI Guiderails** provide **inline** protection against harmful content, PII oversharing or jailbreaking risks. As it is in the flow of agento to LLM interactions it can block or annotate risks in real time.

Microsoft **Defender for AI** provides **post-hoc** detection capabilities and therefore can detect more complex attack patterns. It also brings posture management capabilities with risk indicators and recommendations for mitigation. Defender for AI is reporting to overall **Defender for Cloud** and **Microsoft Sentinel** for correlation with other security signals and overall SOC monitoring.

</details>

## Evals
<details>
<summary>
Evaluations are critical success factor for building high-quality agents because what cannot be measured cannot be improved. 
</summary>

---

### Copilot Studio
has build-in eval capabilities.

### Foundry Agent Service
comes with rich built-in evaluations and red teaming capabilities including offline and batch evals as well as continuous evaluations and CI/CD integration

More over all Foundry agent types do support API access and can therefore be evaluated with any eval framework such as DeepEval or LangFuse.

</details>



## CI/CD
<details>
<summary>
Agents need to be continuously improved and updated and for that we need process to test and implement changes.
</summary>

---

**Copilot Studio** supports CI/CD capabilities via Power Platform ALM

**Foundry Agent Service** fully supports any CI/CD for all types of agents - even declarative agents can be managed effectively via SDK.

</details>




