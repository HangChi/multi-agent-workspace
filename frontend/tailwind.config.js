/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // 深色模式由 <html class="dark"> 控制（首屏在 index.html 内联脚本里先判一次，避免闪白）
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        /**
         * 品牌蓝：通过 CSS 变量取值，深色模式下 50~300 翻成深蓝底，400~900 保持饱和。
         * 于是 bg-brand-50 / text-accent 这类写法两套主题都成立，无需逐处写 dark:。
         */
        brand: {
          50: 'rgb(var(--brand-50) / <alpha-value>)',
          100: 'rgb(var(--brand-100) / <alpha-value>)',
          200: 'rgb(var(--brand-200) / <alpha-value>)',
          300: 'rgb(var(--brand-300) / <alpha-value>)',
          400: 'rgb(var(--brand-400) / <alpha-value>)',
          500: 'rgb(var(--brand-500) / <alpha-value>)',
          600: 'rgb(var(--brand-600) / <alpha-value>)',
          700: 'rgb(var(--brand-700) / <alpha-value>)',
          800: 'rgb(var(--brand-800) / <alpha-value>)',
          900: 'rgb(var(--brand-900) / <alpha-value>)',
          950: 'rgb(var(--brand-950) / <alpha-value>)',
          DEFAULT: 'rgb(var(--brand-600) / <alpha-value>)',
        },
        /** 品牌前景色：浅色下深蓝字，深色下浅蓝字 */
        accent: 'rgb(var(--accent) / <alpha-value>)',

        /** 页面底色 */
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        /** 卡片 / 弹层 / 输入框表面 */
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          muted: 'rgb(var(--surface-muted) / <alpha-value>)',
          subtle: 'rgb(var(--surface-subtle) / <alpha-value>)',
          inset: 'rgb(var(--surface-inset) / <alpha-value>)',
        },
        /** 描边 / 分割线 */
        line: {
          DEFAULT: 'rgb(var(--line) / <alpha-value>)',
          strong: 'rgb(var(--line-strong) / <alpha-value>)',
          subtle: 'rgb(var(--line-subtle) / <alpha-value>)',
        },
        /** 文字层级：ink > soft > muted > faint */
        ink: {
          DEFAULT: 'rgb(var(--ink) / <alpha-value>)',
          soft: 'rgb(var(--ink-soft) / <alpha-value>)',
          muted: 'rgb(var(--ink-muted) / <alpha-value>)',
          faint: 'rgb(var(--ink-faint) / <alpha-value>)',
        },
      },
      fontFamily: {
        /**
         * 拉丁字形用自托管的 Instrument Sans（可变字重 400–700），
         * 中文仍交给 PingFang / 微软雅黑——中文 Web 字体动辄几 MB，
         * 而 CJK 的系统字形在各自平台上本就是最优解。
         * 于是标题、数字、模型名、ID 有了辨识度，中文排版不降级。
         */
        sans: [
          '"Instrument Sans"',
          '-apple-system',
          'BlinkMacSystemFont',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"Noto Sans SC"',
          'sans-serif',
        ],
        /** 数字/指标专用的拉丁展示字体（与正文同族，靠字重与字距拉开层级） */
        display: [
          '"Instrument Sans"',
          '-apple-system',
          'BlinkMacSystemFont',
          '"PingFang SC"',
          'sans-serif',
        ],
        mono: [
          '"JetBrains Mono"',
          '"SF Mono"',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'Monaco',
          'monospace',
        ],
      },
      /**
       * 字阶（全站只允许这四档 + Tailwind 内置档）：
       * micro 12px 元信息 / ui 13px 界面文字 / body 14px 卡片正文与标题 /
       * page-title 20px 页面主标题。
       *
       * 此前页面里散落着 10/10.5/11/11.5/12.5/13/13.5px 七种随手写的字号，
       * 层级互相打架，是「显得脏」的主要原因之一。收敛之后，
       * 「这是标题 / 这是正文 / 这是元信息」不再需要靠颜色和加粗去猜。
       */
      fontSize: {
        micro: ['0.75rem', { lineHeight: '1.125rem' }],
        ui: ['0.8125rem', { lineHeight: '1.25rem' }],
        body: ['0.875rem', { lineHeight: '1.375rem' }],
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        /** 页面主标题：主流工作台（Linear / Vercel）用 20px 而非 28px，
            标题抢戏会让内容区的信息密度显得更低 */
        'page-title': ['1.25rem', { lineHeight: '1.75rem', letterSpacing: '-0.01em' }],
        'page-title-sm': ['1.125rem', { lineHeight: '1.625rem', letterSpacing: '-0.01em' }],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(var(--shadow) / 0.04), 0 1px 3px 0 rgb(var(--shadow) / 0.05)',
        /** 悬浮面板：更长的扩散、更低的透明度，避免「脏灰边」 */
        panel:
          '0 1px 2px 0 rgb(var(--shadow) / 0.04), 0 12px 32px -20px rgb(var(--shadow) / 0.22)',
        pop: '0 24px 48px -24px rgb(var(--shadow) / 0.28), 0 8px 20px -12px rgb(var(--shadow) / 0.16)',
        'btn-primary':
          '0 1px 2px 0 rgb(var(--brand-800) / 0.24), inset 0 1px 0 0 rgb(255 255 255 / 0.16)',
        field: 'inset 0 1px 2px 0 rgb(var(--shadow) / 0.04)',
        /** 侧栏激活项的内发光，替代生硬的左边框 */
        'nav-active': 'inset 0 1px 0 0 rgb(255 255 255 / 0.6), 0 1px 2px 0 rgb(var(--brand-800) / 0.08)',
      },
      borderRadius: {
        xl: '0.875rem',
        '2xl': '1.125rem',
        '3xl': '1.5rem',
      },
      spacing: {
        /** 侧栏宽度（展开 / 收起），布局与内容偏移共用一份真实来源 */
        rail: '4.75rem',
        sidebar: '16rem',
      },
      transitionTimingFunction: {
        'out-expo': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'fade-in-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pop-in': {
          from: { opacity: '0', transform: 'translateY(10px) scale(0.975)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'slide-in-right': {
          from: { opacity: '0', transform: 'translateX(16px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'slide-in-left': {
          from: { opacity: '0', transform: 'translateX(-16px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgb(16 185 129 / 0.45)' },
          '70%': { boxShadow: '0 0 0 6px rgb(16 185 129 / 0)' },
          '100%': { boxShadow: '0 0 0 0 rgb(16 185 129 / 0)' },
        },
        indeterminate: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(320%)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.18s ease-out both',
        'fade-in-up': 'fade-in-up 0.24s cubic-bezier(0.16, 1, 0.3, 1) both',
        'pop-in': 'pop-in 0.22s cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-in-right': 'slide-in-right 0.22s cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-in-left': 'slide-in-left 0.22s cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.6s infinite',
        'pulse-ring': 'pulse-ring 2s infinite',
        indeterminate: 'indeterminate 1.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
