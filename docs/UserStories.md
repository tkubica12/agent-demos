# User stories for agentic AI

<details>
<summary>As developer I want to be able to ...</summary>

- gain access to LLMs of my choice with quota and performance I need
- use playground where I can quickly test my agentic AI ideas
- use declarative methods to define my agent or workflow so I can prototype quickly without need to go deep into code
- create agents using open source framework of my choice
- have catalog of company tools (MCP servers) that I can quickly test and access from my agent
- deep observability so I can drill down into individual turns in conversation to troubleshoot and understand how my agent is working
- create evals so I can automatically test my agent's performance (quality, coherence, groundedness, speed, personality, ...) and track it over time
- have catalog of other agents that I can use in my multi-agent system
- create agent that can be accessed in different channels (e.g. web, mobile, slack, etc.) without need to rewrite code for each channel
- publish my agent into Microsoft 365 environment so it can be used by my colleagues
- have versioned-control catalog of prompts so I can track changes, reuse or do A/B testing on prompts
- have agent access to user stuff in Microsoft 365 - emails, Teams messages, calendar, files
- have agent access to institutional knowledge - e.g. company wiki, internal databases, knowledge bases, etc.
- have agent access to business data
- be able to work with full lifecycle of agent development and CI/CD support
- ability to work with memory for my agents
- apply policy-based guardrails at user input, tool call, tool response, and output stages
- implement human-in-the loop
- understand cost implications of my agent behavior with drill down into individual conversations so I can optimize for cost
- have identity for agent so I can work both on user behalf (OBO) as well as build autonomous workflows with agent identity that can have its own permissions and access
</details>

<details>
<summary>As administrator I want to be able to ...</summary>

- manage access to LLMs and tools for my developers
- monitor usage of LLMs and tools by my developers
- manage costs associated with LLM usage
- manage access to agents created by developers for different users in my organization
- configure permissions for agents to access different data sources and tools or other agents
- manage curated catalog of internal, Microsoft and 3rd party agents
- manage curated catalog of internal, Microsoft and 3rd party tools
- manage agents via different zones/environments to isolate development, testing and production stages
- enforce data governance, compliance, and behavioral policies across the agent lifecycle
- policy-driven publishing process for agents
- generate usage, compliance, and cost reports for agents
</details>

<details>
<summary>As security person I want to be able to ...</summary>

- ensure that all agents comply with my organization's security policies
- monitor agent activity for potential security threats
- understand security posture of agentic AI usage in my organization
- have tools to investigate and respond to security incidents involving agents
- have controls to restrict agent access to sensitive data and tools
- enforce data security to prevent overshearing and data leakage
- have audit logs of agent activity for compliance and forensic purposes
- enforce secure development practices through CI/CD, automated scans, and guardrails
- enforce strong authentication and access controls for agents and implement zero trust principles
- ensure that agents are regularly updated and patched to address security vulnerabilities
- regularly and automatically run red-teaming and adversarial simulations
</details>

<details>
<summary>As business user I want to be able to ...</summary>

- easily find and use agents that can help me with my work
- trust that the agents I use are secure and compliant with company policies
- create my own agents and workflows without needing to know how to code
- publish agents I create so that my colleagues can use them
- have agent access to user data in Microsoft 365 - emails, Teams messages, calendar, files
- have agent access to institutional knowledge - e.g. company wiki, internal databases, knowledge bases, etc.
- have agent access to business data
- have agent access to curated list of company-approved tools
- understand what data and tools an agent uses
- see references or sources behind agent outputs
- provide feedback on agent quality or errors

</details>
