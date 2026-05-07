export function SettingsPageLead({
  title,
  hint
}: {
  title: string;
  hint: string;
}) {
  return (
    <div className="settings-page-lead">
      <h2 className="settings-page-lead-title">{title}</h2>
      <p className="settings-page-lead-hint">{hint}</p>
    </div>
  );
}
