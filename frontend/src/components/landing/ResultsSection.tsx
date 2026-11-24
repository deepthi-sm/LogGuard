import { motion } from "framer-motion";
import CountUp from "react-countup";
import { useInView } from "react-intersection-observer";
import { AnimatedSection, containerVariants, itemVariants } from "./AnimatedSection";

interface Kpi {
  value: number;
  decimals: number;
  suffix: string;
  prefix?: string;
  title: string;
  subtitle: string;
}

const KPIS: Kpi[] = [
  {
    value: 1.0,
    decimals: 3,
    suffix: "",
    title: "F1, OpenStack",
    subtitle: "held-out slice, Model B",
  },
  {
    value: 0.516,
    decimals: 3,
    suffix: "",
    title: "F1, HDFS",
    subtitle: "held-out slice, Model B",
  },
  {
    value: 0.914,
    decimals: 3,
    suffix: "",
    title: "F1, Apache",
    subtitle: "never seen in training",
  },
  {
    value: 100,
    decimals: 0,
    suffix: "%",
    title: "on-prem inference",
    subtitle: "no third-party calls",
  },
];

interface ResultRow {
  left: string;
  a: string;
  b: string;
}

const F1_ROWS: ResultRow[] = [
  { left: "OpenStack (held-out)", a: "0.936", b: "1.000" },
  { left: "HDFS (held-out)", a: "0.000", b: "0.516" },
  { left: "Apache (never seen)", a: "0.984", b: "0.914" },
];

const AUC_ROWS: ResultRow[] = [
  { left: "OpenStack (held-out)", a: "0.996", b: "1.000" },
  { left: "HDFS (held-out)", a: "0.396", b: "0.670" },
  { left: "Apache (never seen)", a: "0.458", b: "0.368" },
];

function ResultTable({ title, rows }: { title: string; rows: ResultRow[] }) {
  return (
    <motion.div variants={itemVariants} className="bg-card p-7">
      <h3 className="text-[14px] font-medium text-primary">{title}</h3>
      <div className="mt-5 divide-y-[0.5px] divide-border-subtle">
        <div className="flex items-center justify-between py-2.5 text-[11px] uppercase tracking-[0.08em] text-tertiary">
          <span>Test slice</span>
          <span className="flex gap-6">
            <span className="w-12 text-right">A</span>
            <span className="w-12 text-right">B</span>
          </span>
        </div>
        {rows.map((row) => (
          <div key={row.left} className="flex items-center justify-between py-2.5">
            <span className="text-[13px] text-tertiary">{row.left}</span>
            <span className="flex gap-6 font-mono text-[13px] tabular-nums">
              <span className="w-12 text-right text-tertiary">{row.a}</span>
              <span className="w-12 text-right text-iris">{row.b}</span>
            </span>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

export function ResultsSection() {
  const { ref: triggerRef, inView } = useInView({
    threshold: 0.25,
    triggerOnce: true,
  });

  return (
    <AnimatedSection
      id="results"
      direction="right"
      className="px-[56px] py-[96px]"
    >
      <div className="mx-auto max-w-[1100px]" ref={triggerRef}>
        <div className="text-[11px] uppercase tracking-[0.08em] text-tertiary">
          Results
        </div>
        <h2 className="mt-3 max-w-[800px] font-display text-[40px] font-medium leading-[1.15] tracking-[-0.02em] text-primary">
          Numbers from a real evaluation.
        </h2>
        <p className="mt-5 max-w-[680px] text-[15px] leading-[1.65] text-secondary">
          Model A is trained on OpenStack only. Model B is trained on
          OpenStack plus a 100,000-line HDFS sample. Both are scored on
          held-out OpenStack and HDFS slices and on Apache logs that neither
          model saw in training. Apache is 99% anomalous, so its F1 is high
          while its AUC is low; we report both.
        </p>

        {/* KPI strip — hairline above + below */}
        <div className="mt-12 grid grid-cols-2 gap-x-9 gap-y-9 border-y-[0.5px] border-border-subtle py-9 md:grid-cols-4">
          {KPIS.map((k, i) => (
            <div key={k.title}>
              <div className="font-display text-[56px] font-medium leading-none tracking-[-0.03em] text-iris tabular-nums">
                {inView ? (
                  <CountUp
                    end={k.value}
                    decimals={k.decimals}
                    duration={1.5}
                    delay={i * 0.1}
                  />
                ) : (
                  (0).toFixed(k.decimals)
                )}
                {k.suffix && (
                  <span className="ml-0.5 text-[28px]">{k.suffix}</span>
                )}
              </div>
              <div className="mt-2 text-[12px] font-medium text-primary">
                {k.title}
              </div>
              <div className="mt-0.5 text-[11px] text-tertiary">
                {k.subtitle}
              </div>
            </div>
          ))}
        </div>

        {/* Comparison cards */}
        <motion.div
          variants={containerVariants}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, amount: 0.15 }}
          className="mt-12 grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border-subtle md:grid-cols-2"
        >
          <ResultTable title="F1 score" rows={F1_ROWS} />
          <ResultTable title="AUC (threshold-free)" rows={AUC_ROWS} />
        </motion.div>
      </div>
    </AnimatedSection>
  );
}
