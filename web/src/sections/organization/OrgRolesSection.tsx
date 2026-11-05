import { useEffect, useState } from "react";

import { api } from "../../api";
import { notify } from "../../page";

type OrgRole = {
  name: string;
  title?: string;
  summary?: string;
  description?: string;
  required?: boolean;
};

type OrgRoles = {
  can_manage?: boolean;
  roles?: OrgRole[];
  enforced?: string[];
};

export function OrgRolesSection() {
  const [body, setBody] = useState<OrgRoles | null>(null);

  useEffect(() => {
    function onSettings(event: Event) {
      setBody((event as CustomEvent<OrgRoles>).detail || {});
    }
    const cached = (window as OrgRolesWindow).__lbwOrgSettings;
    if (cached) setBody(cached);
    document.addEventListener("lbw-org-settings", onSettings);
    return () => document.removeEventListener("lbw-org-settings", onSettings);
  }, []);

  const roles = body?.roles || [];
  const enforced = new Set(body?.enforced || []);
  const manage = Boolean(body?.can_manage);

  async function save(name: string, on: boolean) {
    try {
      const next = await api<OrgRoles>("/api/org/roles", {
        method: "PUT",
        body: JSON.stringify({ role_name: name, enforced: on }),
      });
      (window as OrgRolesWindow).renderOrgSettings?.(next);
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "could not update the role", "Organization");
    }
  }

  return (
    <>
      {roles.map((role) => {
        const tip = role.required
          ? "Builder and QA stay on for a project. This switch keeps the role on everywhere."
          : role.summary || role.description || role.name;
        return (
          <div className="skill-row" key={role.name}>
            <input
              type="checkbox"
              className="org-switch"
              checked={enforced.has(role.name)}
              disabled={!manage}
              data-org-role={role.name}
              aria-label={`Enforce ${role.title || role.name}`}
              onChange={(event) => void save(role.name, event.target.checked)}
            />
            <div className="skill-row-copy">
              <strong title={tip}>{role.title || role.name}</strong>
            </div>
          </div>
        );
      })}
    </>
  );
}

type OrgRolesWindow = Window & {
  __lbwOrgSettings?: OrgRoles;
  renderOrgSettings?: (body: OrgRoles) => void;
};
