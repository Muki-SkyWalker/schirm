(ns schirm-cljs.term
  (:require [cljs.core.async :as async
             :refer [<! >! chan close! sliding-buffer put! alts!]]

            [clojure.string :as string]

            [schirm-cljs.screen-tests :as tests]

            [schirm-cljs.screen :as screen]
            [schirm-cljs.keys :as keys]
            [schirm-cljs.dom-utils :as dom-utils])

  (:require-macros [cljs.core.async.macros :as m :refer [go alt!]]))

;; events -> chan
;; socket-messages -> chan

(defn create-styled-string [string attrs]
  (screen/StyledString. string (apply screen/->CharacterStyle attrs)))

(defn create-fragment-from-lines
  "Create a document fragment from a seq of seqs of raw segments.

  Raw segments are tuples of (string, class-string) forming the basic
  parts of a line."
  [lines]
  (let [fragment (.createDocumentFragment js/document)]
    (doseq [raw-segments lines]
      (let [line (.createElement js/document "div")]
        (doseq [[string class] (if (empty? raw-segments)
                                 [[" " ""]] ;; empty line
                                 raw-segments)]
          (let [segment (.createElement js/document "span")]
            (set! (.-className segment) class)
            (set! (.-textContent segment) string)
            (.appendChild line segment)))
        (.appendChild fragment line)))
    fragment))

(defn invoke-screen-method [state scrollback-screen alt-screen msg]
  (let [[meth & args] msg
        screen (if (:alt-mode state) alt-screen scrollback-screen)]
    (case meth
      "set-line-origin" (do (apply screen/set-origin screen args)
                            state)
      "reset"  (do (screen/reset scrollback-screen (nth args 0))
                   (screen/reset alt-screen (nth args 0))
                   state)
      "resize" (do (screen/set-size scrollback-screen (nth args 0))
                   (screen/set-size alt-screen (nth args 0))
                   state)
      "insert" (let [[line, col, string, attrs] args
                     ss (create-styled-string string attrs)]
                 (screen/update-line screen
                                     line
                                     #(screen/line-insert % ss col))
                 state)
      "insert-overwrite" (let [[line, col, string, attrs] args
                               ss (create-styled-string string attrs)]
                           (screen/update-line screen
                                               line
                                               #(screen/line-insert-overwrite % ss col))
                           state)
      "remove" (let [[line, col, n] args]
                 (screen/update-line screen
                                     line
                                     #(screen/line-remove % col n))
                 state)
      "insert-line" (do (screen/insert-line screen (screen/create-line []) (nth args 0))
                        state)
      "append-line" (do (screen/append-line screen (screen/create-line []))
                        state)
      "append-many-lines" (let [lines (nth args 0 [])]
                            (screen/append-line screen (create-fragment-from-lines lines))
                            state)
      "remove-line" (do (screen/remove-line screen (nth args 0))
                        state)
      "adjust" (do (screen/adjust screen)
                   state)
      "cursor" (let [[line, col] args]
                 (screen/set-cursor screen line col)
                 state)
      "enter-alt-mode" (do (screen/show scrollback-screen false)
                           (screen/show alt-screen true)
                           (assoc state :alt-mode true))
      "leave-alt-mode" (do (screen/show scrollback-screen true)
                           (screen/show alt-screen false)
                           (screen/reset alt-screen)
                           (assoc state :alt-mode false))
      "set-title" (do (set! (.-title js/document) (nth args 0))
                      state))))

(def chords {;; browsers have space and shift-space bound to scroll page down/up
             [:space] (fn [send] (send {:string " "}) true)
             [:shift :space] (fn [send] (send {:string " "}) true)
             ;; ignore F12 as this opens the browsers devtools
             [:F12] (fn [send] false)})

(defn setup-keys [send-chan]
  (let [send-key (fn [key]
                   (let [message {:name :keypress :key key}]
                     (put! send-chan (.stringify js/JSON (clj->js message)))))]
    (keys/setup-window-key-handlers js/window chords send-key)))

(defn setup-screens [parent-element input-chan]
  (let [[scrollback-screen alt-screen :as screens] (screen/create-screens parent-element)
        state (atom {})]
    (screen/show alt-screen false)
    (go
     (loop []
       (doseq [message (<! input-chan)]
         (reset! state (invoke-screen-method @state scrollback-screen alt-screen message)))
       (recur)))
    screens))

(defn setup-resize [container ws-send screens]
  (let [resize-screen (fn [] (let [pre (.-element (first (filter #(.-visible %) screens)))
                                   new-size (screen/container-size container pre)
                                   message (clj->js (assoc new-size :name :resize))]
                               (put! ws-send (.stringify js/JSON message))))]
    (set! (.-onresize js/window) resize-screen)
    (resize-screen)))

(defn setup-websocket [url in out]
  (let [ws (js/WebSocket. url)]
    (set! (.-onmessage ws)
          (fn [ev]
            (if (not= "" (.-data ev))
              (put! out (.parse js/JSON (.-data ev))))))
    (set! (.-onopen ws)
          #(go
            (loop []
              (let [msg (<! in)]
                (.send ws msg)
                (recur)))))))

(defn setup-terminal []
  (let [ws-send  (chan)
        ws-recv (chan)
        ws-url (format "ws://%s" (-> js/window .-location .-host))
        container (dom-utils/select 'body)
        screens (setup-screens container ws-recv)]
    (setup-keys ws-send)
    (setup-resize container ws-send screens)
    (setup-websocket ws-url ws-send ws-recv)))

(defn init []
  (dom-utils/document-ready setup-terminal))

(defn tests []
  (dom-utils/document-ready (fn []
                              (doseq [result (tests/run-tests)]
                                (.log js/console (str result))))))

(init)
