import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

/**
 * Перехват исключений отрисовки.
 *
 * До этого границы не было нигде во всём `dashboard/src` (аудит §1), и любое
 * исключение в render давало белый экран без единого слова. Автор Broadcasts
 * сам оставил комментарий, что ловил такое дважды; в Statistics.tsx до сих пор
 * лежит мина — useMemo после раннего return.
 *
 * Границы две, и это осознанно:
 *  - общая вокруг всего приложения — последняя сетка, если рухнет сама
 *    оболочка;
 *  - отдельная вокруг области контента — отказ одного экрана не должен гасить
 *    сайдбар, шапку и мобильную навигацию: с рабочей оболочкой человек уйдёт
 *    на другой экран сам, без перезагрузки.
 *
 * `resetKey` — обычно pathname. Сменился путь, значит человек ушёл на другой
 * экран, и старую ошибку надо забыть, иначе новый экран не отрисуется.
 */

interface Props {
  children: ReactNode;
  /** Смена значения сбрасывает пойманную ошибку. */
  resetKey?: string;
  /** Как показывать отказ: во весь экран или врезкой внутри оболочки. */
  variant?: "page" | "content";
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Единственное место, где ошибка вообще попадает в консоль: React 18
    // после getDerivedStateFromError гасит собственный вывод.
    console.error("[dashboard] ошибка отрисовки:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const full = this.props.variant !== "content";

    return (
      <div
        role="alert"
        className={
          full
            ? "grid h-full place-items-center p-6"
            : "grid min-h-[50vh] place-items-center p-6"
        }
      >
        <div className="w-full max-w-md rounded-lg border border-danger/30 bg-bg-card p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden />
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-fg">
                {full ? "Панель упала" : "Экран не открылся"}
              </h2>
              <p className="mt-1 text-base text-fg-muted">
                {full
                  ? "Ошибка в коде интерфейса, а не в данных. Данные не пострадали — ничего не отправлено и не изменено."
                  : "Ошибка в коде этого экрана. Остальные разделы работают — откройте любой из меню."}
              </p>
              {/* Текст ошибки показываем: панель видят три администратора, и
                  им проще переслать строку, чем описывать словами. */}
              <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-bg-subtle p-2 font-mono text-xs text-fg-muted">
                {error.message || String(error)}
              </pre>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => this.setState({ error: null })}
                >
                  <RotateCcw className="h-4 w-4" aria-hidden />
                  Показать снова
                </button>
                {full && (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => window.location.reload()}
                  >
                    Перезагрузить страницу
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
