from brain import ElderlyAI

def main():
    ai = ElderlyAI()
    elder_id = "elder_001"
    
    print("🌟 懷舊之旅啟動：AI 已載入禁忌詞防護網。")
    current_response = ai.generate_response(elder_id, "開始在當年的生活")
    
    while True:
        print("\n" + "="*50)
        print(current_response)
        print("="*50)
        
        if ai.turn_count >= 4:
            print("\n🌙 故事已進入深夜，願這些美好的回憶伴您入眠。")
            break
            
        user_input = input("\n請輸入回憶 (exit 離開)：").strip()
        if user_input.lower() == 'exit': break
        current_response = ai.continue_story(elder_id, user_input)

if __name__ == "__main__":
    main()