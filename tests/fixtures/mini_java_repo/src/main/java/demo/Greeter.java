package demo;

public class Greeter {
    public String greet(String name) {
        return "hi " + name;
    }

    public static void main(String[] args) {
        System.out.println(new Greeter().greet("x"));
    }
}
