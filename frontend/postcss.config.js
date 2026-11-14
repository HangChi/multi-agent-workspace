/** PostCSS 配置：让 Tailwind 指令被实际处理（缺失时 CSS 只输出裸 @tailwind 指令，页面零样式）。 */
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
