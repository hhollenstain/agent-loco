import { useEffect, useState } from "react";

import { api } from "../../api";
import { notify } from "../../page";
import { repoLabel } from "../../repoLabel";
import type { OrgCatalog } from "../models/types";

type SkillEntry = {
  name: string;
  enabled: boolean;
};

type SkillRepo = {
  id: string;
  url: string;
  sha?: string;
  can_sync?: boolean;
  enabled?: boolean;
  skills?: string[];
  entries?: SkillEntry[];
};

type SkillInfo = {
  name?: string;
  description?: string;
  html?: string;
  body?: string;
};

type OrgSkills = OrgCatalog & {
  org_id?: string;
  skill_repos?: SkillRepo[];
};

const INFO_ICON = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path
      d="M9.6 9.4a2.5 2.5 0 1 1 3.5 2.28c-.72.36-1.1.86-1.1 1.72V14"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeWidth="1.8"
    />
    <circle cx="12" cy="17.2" r="0.9" fill="currentColor" />
  </svg>
);

export function OrgSkillsSection() {
  const [body, setBody] = useState<OrgSkills | null>(null);
  const [url, setUrl] = useState("");
  const [openRepos, setOpenRepos] = useState<Record<string, boolean>>({});
  const [infoName, setInfoName] = useState("");
  const [info, setInfo] = useState<SkillInfo | null>(null);
  const [infoError, setInfoError] = useState("");

  useEffect(() => {
    function onSettings(event: Event) {
      setBody((event as CustomEvent<OrgSkills>).detail || {});
    }
    const cached = (window as OrgSkillsWindow).__lbwOrgSettings as OrgSkills | undefined;
    if (cached) setBody(cached);
    document.addEventListener("lbw-org-settings", onSettings);
    return () => document.removeEventListener("lbw-org-settings", onSettings);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && infoName) setInfoName("");
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [infoName]);

  const repos = body?.skill_repos || [];
  const manage = Boolean(body?.can_manage);

  async function addRepo() {
    try {
      const next = await api<OrgSkills>("/api/org/skill-repos", {
        method: "POST",
        body: JSON.stringify({
          scope_type: "tenant",
          scope_id: body?.org_id || "",
          url,
        }),
      });
      setUrl("");
      refresh(next);
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not add the skill repository", "Organization");
    }
  }

  async function resync(repoId: string) {
    try {
      const next = await api<OrgSkills>(`/api/org/skill-repos/${encodeURIComponent(repoId)}/sync`, {
        method: "POST",
      });
      refresh(next);
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not resync the skill repository", "Organization");
    }
  }

  async function setRepoEnabled(repoId: string, enabled: boolean) {
    try {
      refresh(await api<OrgSkills>(`/api/org/skill-repos/${encodeURIComponent(repoId)}`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }));
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not update the skill repository", "Organization");
    }
  }

  async function setSkillEnabled(repoId: string, name: string, enabled: boolean) {
    try {
      refresh(await api<OrgSkills>(
        `/api/org/skill-repos/${encodeURIComponent(repoId)}/skills/${encodeURIComponent(name)}`,
        { method: "PUT", body: JSON.stringify({ enabled }) },
      ));
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not update the skill", "Organization");
    }
  }

  async function removeRepo(repoId: string) {
    try {
      refresh(await api<OrgSkills>(`/api/org/skill-repos/${encodeURIComponent(repoId)}`, { method: "DELETE" }));
      if (infoName) setInfoName("");
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not remove the skill repository", "Organization");
    }
  }

  async function openInfo(repoId: string, name: string) {
    setInfoName(name);
    setInfo(null);
    setInfoError("");
    try {
      const detail = await api<SkillInfo>(
        `/api/org/skill-repos/${encodeURIComponent(repoId)}/skills/${encodeURIComponent(name)}`,
      );
      setInfo(detail);
    } catch (err) {
      setInfoError(err instanceof Error ? err.message : "could not load the skill");
    }
  }

  return (
    <>
      <div className="settings-skill-sources" id="org-skill-repos">
        {repos.length === 0 ? <p className="hint">No skill repositories yet.</p> : null}
        {repos.map((repo) => {
          const short = repo.sha ? repo.sha.slice(0, 7) : "";
          const entries = skillEntries(repo);
          const onCount = entries.filter((entry) => entry.enabled).length;
          const mixed = onCount > 0 && onCount < entries.length;
          const repoOn = entries.length === 0 ? Boolean(repo.enabled) : onCount === entries.length;
          const writable = manage && repo.can_sync !== false;
          const open = Boolean(openRepos[repo.id]);
          const label = repoLabel(repo.url, repo.url);
          return (
            <section className={open ? "org-skill-repo" : "org-skill-repo folded"} key={repo.id} data-org-skill-repo={repo.id}>
              <div className="org-skill-head">
                <button
                  type="button"
                  className="skill-repo-fold"
                  data-org-skill-fold={repo.id}
                  aria-expanded={open ? "true" : "false"}
                  title={short ? `${repo.url} · ${short}` : repo.url}
                  onClick={() => setOpenRepos((current) => ({ ...current, [repo.id]: !current[repo.id] }))}
                >
                  <span className="skill-repo-title">
                    <span className="skill-repo-mark" aria-hidden="true">{open ? "▾" : "▸"}</span>
                    <span>{label}</span>
                  </span>
                  <span className="skill-repo-count">{entries.length}</span>
                </button>
                <div className="org-skill-actions">
                  <input
                    type="checkbox"
                    className="org-switch"
                    data-org-skill-enable={repo.id}
                    aria-label={`Enable ${label}`}
                    checked={repoOn}
                    ref={(node) => {
                      if (node) node.indeterminate = mixed;
                    }}
                    disabled={!writable}
                    onChange={(event) => void setRepoEnabled(repo.id, event.target.checked)}
                  />
                  {writable ? (
                    <button type="button" className="secondary" data-skill-repo-sync={repo.id} onClick={() => void resync(repo.id)}>
                      Resync
                    </button>
                  ) : null}
                  {writable ? (
                    <button type="button" className="secondary" data-org-skill-remove={repo.id} onClick={() => void removeRepo(repo.id)}>
                      Remove
                    </button>
                  ) : null}
                </div>
              </div>
              {open ? (
                <div className="skill-repo-body">
                  {entries.length === 0 ? <p className="hint">No skills loaded yet.</p> : null}
                  {entries.map((entry) => (
                    <div className="skill-row" key={entry.name}>
                      <input
                        type="checkbox"
                        className="org-switch"
                        data-org-skill={entry.name}
                        aria-label={`Enable ${entry.name}`}
                        checked={entry.enabled}
                        disabled={!writable}
                        onChange={(event) => void setSkillEnabled(repo.id, entry.name, event.target.checked)}
                      />
                      <div className="skill-row-copy">
                        <strong>{entry.name}</strong>
                      </div>
                      <button
                        type="button"
                        className="skill-info"
                        data-org-skill-info={entry.name}
                        aria-label={`About ${entry.name}`}
                        aria-expanded={infoName === entry.name ? "true" : "false"}
                        aria-controls="org-skill-info-pop"
                        onClick={() => void openInfo(repo.id, entry.name)}
                      >
                        {INFO_ICON}
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
      <div className="settings-skill-source" id="org-skill-repo-form" hidden={!manage}>
        <input
          id="org-skill-repo-url"
          type="url"
          placeholder="https://github.com/org/skills.git"
          autoComplete="off"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="button" className="secondary" id="add-org-skill-repo" onClick={() => void addRepo()}>
          Add
        </button>
      </div>
      <div className="skill-info-pop" id="org-skill-info-pop" hidden={!infoName}>
        <div className="skill-info-pop-head">
          <h3 id="org-skill-info-title">{info?.name || infoName || "Skill"}</h3>
          <button type="button" className="secondary" id="close-org-skill-info" onClick={() => setInfoName("")}>
            Close
          </button>
        </div>
        <div className="skill-info-pop-body" id="org-skill-info-body">
          {infoError ? <p className="hint">{infoError}</p> : null}
          {!info && !infoError ? <p className="hint">Loading skill…</p> : null}
          {info?.description ? <p className="skill-info-lead">{info.description}</p> : null}
          {info?.html ? (
            <article className="skill-markdown" dangerouslySetInnerHTML={{ __html: info.html }} />
          ) : info?.body ? (
            <pre>{info.body}</pre>
          ) : null}
        </div>
      </div>
    </>
  );
}

function skillEntries(repo: SkillRepo): SkillEntry[] {
  if (repo.entries && repo.entries.length > 0) return repo.entries;
  return (repo.skills || []).map((name) => ({ name, enabled: true }));
}

function refresh(body: OrgSkills) {
  const page = window as OrgSkillsWindow;
  page.renderOrgSettings?.(body);
}

type OrgSkillsWindow = Window & {
  __lbwOrgSettings?: OrgSkills;
  renderOrgSettings?: (body: OrgSkills) => void;
};
