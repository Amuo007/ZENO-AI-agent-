import type { ReactNode } from 'react'
import {
  ArrowRight,
  BookOpen,
  Bot,
  Clock,
  Code,
  ExternalLink,
  Link,
  Lock,
  MessageCircle,
  Mic,
  ShieldCheck,
  Wrench,
} from 'lucide-react'
import { Card, CardTitle, PageHeader } from './ui'

const EXAMPLES: { say: string; what: string }[] = [
  { say: 'What’s on my calendar tomorrow?', what: 'ZENO answers right away' },
  { say: 'In 10 minutes, email Alex that I’m running late.', what: 'A Bit waits, then sends it' },
  { say: 'Tell me when the offer letter arrives.', what: 'A Bit keeps an eye on your inbox' },
  { say: 'Open the PDF Sam sent me and give me the key points.', what: 'A Bit downloads and reads it' },
  { say: 'Compare five note-taking apps and make me a spreadsheet.', what: 'A Bit researches and builds it' },
]

const STEPS: { title: string; text: string }[] = [
  { title: 'You ask', text: 'By voice on the Talk page or in the app, or by typing.' },
  { title: 'ZENO decides', text: 'Quick things it does itself. Bigger jobs go to a Bit, and ZENO keeps talking with you.' },
  { title: 'A Bit works', text: 'On its own locked-down computer: code, files, your apps and the web.' },
  { title: 'It asks if it must', text: 'ZENO reads the question out, and it shows under Waiting on you. Your answer goes back to the Bit.' },
  { title: 'You get the result', text: 'ZENO tells you how it went. Files the Bit made stay in its folder on the Bits page.' },
]

const SKILLS: { icon: typeof Code; title: string; text: string }[] = [
  { icon: Code, title: 'Runs code', text: 'Writes and runs Python on its own locked-down computer.' },
  { icon: BookOpen, title: 'Reads anything', text: 'PDFs, images, documents and the web.' },
  { icon: Link, title: 'Uses your apps', text: 'Gmail, Calendar, Drive and 2,000+ more you connect under Apps.' },
  { icon: Clock, title: 'Waits and watches', text: 'Reminders, new emails, price drops, for hours or days.' },
  { icon: Wrench, title: 'Builds tools', text: 'Saves useful scripts to a toolbox every later Bit can use.' },
  { icon: MessageCircle, title: 'Asks when unsure', text: 'A choice or an approval comes to you before it acts.' },
]

const PRIVACY = [
  'Only you can see your chats, memory, apps, Bits and files.',
  'Each Bit works in its own sandbox and sees only your files.',
  'You can read, edit or turn off what ZENO remembers on the Memory page.',
  'App sign-ins go through Pipedream; ZENO never sees your passwords.',
]

const STACK = ['Claude Sonnet', 'Claude Opus', 'Pipecat', 'Whisper', 'ElevenLabs', 'Docker', 'Pipedream', 'Cloudflare', 'React', 'Kotlin']

const SLIDES = [
  '01-cover',
  '02-problem',
  '03-meet',
  '04-bits',
  '05-demo',
  '06-hood',
  '07-stack',
  '08-impact',
  '09-future',
  '10-thanks',
]

/** What ZENO is and how to use it: the user-facing version of the project README. */
export default function About() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="About ZENO" subtitle="A voice assistant with a team of AI workers. Here’s how it works." />

      <Card className="overflow-hidden">
        <div className="grid grid-cols-1 items-center gap-6 p-6 md:grid-cols-[minmax(0,1fr)_auto] md:p-8">
          <div className="min-w-0">
            <p className="font-display text-[28px] font-bold leading-tight tracking-[-0.02em] md:text-[34px]">
              Talk to it. It gets things done.
            </p>
            <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
              You talk to <b className="text-ink">ZENO</b>. Quick questions it answers itself. Anything bigger it hands to a{' '}
              <b className="text-ink">Bit</b>: an AI worker with its own computer that can code, read files, use your apps, and
              wait or keep watch for hours. ZENO keeps talking with you meanwhile and tells you when it’s done.
            </p>
          </div>
          <div className="flex items-end justify-center gap-2">
            <Character src="/avatars/zeno-idle.webp" name="ZENO" role="The voice" icon={<Mic size={12} />} />
            <ArrowRight className="mb-16 shrink-0 text-faint" size={20} />
            <Character src="/avatars/bit.webp" name="Bits" role="The workers" icon={<Bot size={12} />} />
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <CardTitle>Try saying</CardTitle>
          <ul className="flex flex-col gap-2">
            {EXAMPLES.map(({ say, what }) => (
              <li key={say} className="rounded-2xl bg-raised/70 px-4 py-3">
                <p className="text-[15px] font-medium">“{say}”</p>
                <p className="mt-0.5 text-[13px] text-muted">{what}</p>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5">
          <CardTitle>How a job goes</CardTitle>
          <ol className="flex flex-col">
            {STEPS.map(({ title, text }, i) => (
              <li key={title} className="flex gap-3.5">
                <div className="flex flex-col items-center">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink text-[13px] font-semibold text-canvas">
                    {i + 1}
                  </span>
                  {i < STEPS.length - 1 && <span className="w-px flex-1 bg-line" />}
                </div>
                <div className="min-w-0 pb-4">
                  <p className="text-[15px] font-semibold leading-7">{title}</p>
                  <p className="text-[13px] leading-relaxed text-muted">{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card className="p-5">
        <CardTitle>What a Bit can do</CardTitle>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {SKILLS.map(({ icon: Icon, title, text }) => (
            <div key={title} className="rounded-2xl border border-line p-4">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-raised">
                <Icon size={18} />
              </span>
              <p className="mt-3 text-[15px] font-semibold">{title}</p>
              <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{text}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-5">
        <CardTitle>The big picture</CardTitle>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Diagram src="/docs/system-overview.png" alt="System diagram: the app and dashboard connect through Cloudflare to the ZENO server, which hands jobs to Bits in Docker sandboxes and uses Claude, ElevenLabs, Pipedream and Google sign-in." caption="The whole system" />
          <Diagram src="/docs/agent-flow.png" alt="Flow chart of how you, ZENO and a Bit hand a job back and forth." caption="How ZENO and the Bits work together" />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <CardTitle>Your privacy</CardTitle>
          <ul className="flex flex-col gap-2.5">
            {PRIVACY.map((line) => (
              <li key={line} className="flex gap-2.5 text-[14px] leading-relaxed">
                <ShieldCheck size={17} className="mt-0.5 shrink-0 text-success" />
                {line}
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5">
          <CardTitle>Built with</CardTitle>
          <div className="flex flex-wrap gap-2">
            {STACK.map((name) => (
              <span key={name} className="rounded-full bg-raised px-3 py-1.5 text-[13px] font-medium">
                {name}
              </span>
            ))}
          </div>
          <p className="mt-4 flex items-center gap-2 text-[13px] text-muted">
            <Lock size={14} className="shrink-0" />
            Every Bit gets its own locked-down Docker container while it works.
          </p>
        </Card>
      </div>

      <Card className="p-5">
        <CardTitle
          action={
            <a
              href="https://github.com/Amuo007/ZENO-AI-agent-"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-[13px] text-muted hover:text-ink"
            >
              Source on GitHub <ExternalLink size={12} />
            </a>
          }
        >
          The pitch
        </CardTitle>
        <div className="-mx-5 flex snap-x snap-mandatory scroll-px-5 gap-3 overflow-x-auto px-5 pb-2">
          {SLIDES.map((name, i) => (
            <a
              key={name}
              href={`/docs/slides/${name}.png`}
              target="_blank"
              rel="noreferrer"
              className="w-[280px] shrink-0 snap-start overflow-hidden rounded-xl border border-line transition hover:opacity-90 sm:w-[340px]"
            >
              <img src={`/docs/slides/${name}.png`} alt={`Slide ${i + 1}`} loading="lazy" className="aspect-video w-full object-cover" />
            </a>
          ))}
        </div>
      </Card>
    </div>
  )
}

function Character({ src, name, role, icon }: { src: string; name: string; role: string; icon: ReactNode }) {
  return (
    <div className="flex w-[120px] flex-col items-center text-center md:w-[140px]">
      <img src={src} alt="" className="h-[120px] w-auto object-contain md:h-[140px]" />
      <p className="mt-2 flex items-center gap-1 text-[14px] font-semibold">
        {icon}
        {name}
      </p>
      <p className="text-[12px] text-muted">{role}</p>
    </div>
  )
}

function Diagram({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  return (
    <a href={src} target="_blank" rel="noreferrer" className="group flex min-w-0 flex-col gap-2">
      <div className="flex flex-1 items-center justify-center overflow-hidden rounded-2xl border border-line bg-white p-3">
        <img src={src} alt={alt} loading="lazy" className="max-h-[420px] w-full object-contain transition group-hover:opacity-90" />
      </div>
      <p className="text-[13px] text-muted">{caption} · open full size</p>
    </a>
  )
}
