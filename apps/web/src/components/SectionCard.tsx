import { PropsWithChildren, ReactNode } from "react";

interface SectionCardProps extends PropsWithChildren {
  title: string;
  kicker?: string;
  action?: ReactNode;
}

export default function SectionCard({ title, kicker, action, children }: SectionCardProps) {
  return (
    <section className="section-card">
      <header className="section-head">
        <div>
          {kicker ? <p className="kicker">{kicker}</p> : null}
          <h2>{title}</h2>
        </div>
        {action ? <div>{action}</div> : null}
      </header>
      <div className="section-content">{children}</div>
    </section>
  );
}
