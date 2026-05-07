export function SettingsPageLead({
  title,
  hint
}: {
  title: string;
  hint: string;
}) {
  return (
    <div className="settings-page-lead">
      <h2>{title}</h2>
      <p>{hint}</p>
    </div>
  );
}
